from __future__ import annotations

from dataclasses import dataclass

from .config import GenerationConfig
from .schemas import PeriodType, Scene, Storyboard, Visual
from .security import redact
from .story_selection import MetricInsight, select_story


@dataclass(frozen=True)
class ModelResult:
    storyboard: Storyboard
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    fallback_reason: str = ""


def offline_storyboard(period: PeriodType) -> Storyboard:
    period_word = "daily" if period == PeriodType.DAILY else "weekly"
    narration = (
        f"Welcome to your {period_word} reflection, a private summary using only the categories you "
        "deliberately allowed and focusing on broad patterns rather than isolated readings; notice "
        "what felt steady, because consistency can offer useful context when no single number tells "
        "the whole story, then look for movement across the period and remember that change is an "
        "observation, not a diagnosis; consider what was happening around your routines, energy, "
        "rest, and activity, and choose one realistic action for the next period, such as protecting "
        "bedtime, taking a short walk, or planning a quiet pause; keep the experiment easy to repeat, "
        "compare the same broad signals next time, and remember that your data stays under your "
        "control while external services remain optional; this reflection is informational, not "
        "medical advice, so if anything concerns you, use your judgment and speak with a qualified "
        "professional; for now, carry forward one helpful observation and one kind next step."
    )
    scenes = [
        Scene(
            start_seconds=0,
            duration_seconds=10,
            scene_type="title",
            heading=f"Your {period_word.title()} Reflection",
            body="Private, calm, and descriptive.",
            visual=Visual(kind="gradient", data_reference="period"),
        ),
        Scene(
            start_seconds=10,
            duration_seconds=10,
            scene_type="metric",
            heading="What Stayed Steady",
            body="Look for useful consistency across allowed categories.",
            visual=Visual(kind="chart", data_reference="aggregates"),
        ),
        Scene(
            start_seconds=20,
            duration_seconds=10,
            scene_type="timeline",
            heading="What Shifted",
            body="Changes are observations, not diagnoses.",
            visual=Visual(kind="timeline", data_reference="trend"),
        ),
        Scene(
            start_seconds=30,
            duration_seconds=10,
            scene_type="reflection",
            heading="Add Context",
            body="Routines, rest, activity, and ordinary events all matter.",
            visual=Visual(kind="icon_grid", data_reference="context"),
        ),
        Scene(
            start_seconds=40,
            duration_seconds=10,
            scene_type="recommendation",
            heading="One Kind Next Step",
            body="Choose something small, realistic, and repeatable.",
            visual=Visual(kind="gradient", data_reference="recommendation"),
        ),
        Scene(
            start_seconds=50,
            duration_seconds=10,
            scene_type="closing",
            heading="Keep Learning Gently",
            body="Your data stays under your control.",
            visual=Visual(kind="gradient", data_reference="closing"),
        ),
    ]
    return Storyboard(
        title=f"Your {period_word.title()} Reflection",
        period_type=period,
        summary="A privacy-controlled reflection built from allowed aggregate categories.",
        narration=narration,
        scenes=scenes,
        safety_notes=["Descriptive only", "Not medical advice"],
        data_categories_used=[],
    )


def personalized_storyboard(period: PeriodType, summary: dict[str, object]) -> Storyboard:
    selection = select_story(summary)
    if selection is None:
        return offline_storyboard(period)
    period_word = "daily" if period == PeriodType.DAILY else "weekly"

    def exact_narration(text: str, target: int = 150) -> str:
        words = text.split()
        filler = (
            "Keep the experiment small repeatable and kind then return to the same pattern next time"
        ).split()
        while len(words) < target:
            words.extend(filler[: target - len(words)])
        return " ".join(words[:target]).rstrip(";,:.") + "."

    category = selection.primary.category
    if period == PeriodType.DAILY:
        narration = exact_narration(
            f"The clearest signal today came from your {category} pattern; it appears first because "
            "it changed more meaningfully than the other available categories; next the three "
            "headline cards place that signal beside the rest of your day, so the scale stays "
            "personal rather than universal; the moving line then shows how the main pattern "
            "developed across the period, while the comparison scene separates observation from "
            "explanation; two signals can move together without one causing the other; the final "
            "pattern card highlights what stayed steady and what may be worth watching tomorrow; "
            "choose one modest experiment rather than chasing every number, and see whether the "
            "same shape returns; this is a private reflection, not medical advice; the useful goal "
            "is not a perfect score but one clearer piece of context for your next day; finish by "
            "carrying forward the most repeatable routine and letting the rest remain an observation."
        )
    else:
        narration = exact_narration(
            f"The strongest weekly story came from your {category} pattern; the opening highlights "
            "the clearest deviation, then the headline cards show which signals deserve attention "
            "without treating every sensor equally; the seven day view reveals the shape of the "
            "week, including steadier periods and the point that stood furthest from your own "
            "baseline; the next comparison places two related observations side by side, but their "
            "timing does not prove that either caused the other; consistency matters here as much "
            "as intensity; use the final pattern to choose one next week experiment that is small "
            "enough to repeat and simple enough to evaluate; this is informational reflection, not "
            "medical advice; your data stays local to the visual story, and the closing asks only "
            "for curiosity; keep the routine that looked most stable, watch the unusual change, and "
            "return next week to see whether the same relationship appears again."
        )

    def count(key: str) -> int:
        value = summary.get(key, 0)
        return value if isinstance(value, int) else 0

    discovered = count("discovered_sensor_count")

    def payload(item: MetricInsight) -> dict[str, object]:
        return {
            "label": item.label,
            "current": item.current,
            "comparison": item.comparison,
            "category": item.category,
            "series": list(item.series),
            "numeric_value": item.value,
            "baseline": item.baseline,
            "unit": item.unit,
        }

    def scene(
        scene_id: str,
        start: float,
        duration: float,
        scene_type: str,
        kind: str,
        heading: str,
        body: str,
        data: dict[str, object],
        caption: str,
        *,
        cue: str = "none",
    ) -> Scene:
        return Scene(
            scene_id=scene_id,
            start_seconds=start,
            duration_seconds=duration,
            scene_type=scene_type,
            heading=heading[:80],
            body=body[:240],
            visual=Visual(kind=kind, data_reference=scene_id, payload=data),
            layout=kind,
            accent=str(data.get("category", category)),
            caption=caption[:120],
            audio_cue=cue,
            accessibility_description=f"Animated {kind.replace('_', ' ')} showing {heading}"[:240],
        )

    primary = selection.primary
    unusual = selection.unusual or primary
    pair = selection.comparison or (
        primary,
        selection.headlines[1] if len(selection.headlines) > 1 else primary,
    )
    cards = [payload(item) | {"label": item.label} for item in selection.headlines]
    glance_body = (
        f"{len(cards)} useful signal{'s' if len(cards) != 1 else ''}, "
        "compared with your own recent range."
    )
    has_pair = selection.comparison is not None and pair[0] != pair[1]

    if period == PeriodType.DAILY:
        scenes = [
            scene(
                "daily-hook",
                0,
                4,
                "title",
                "hook",
                f"{primary.label} stood out today",
                primary.comparison,
                payload(primary) | {"badge": "Today’s clearest signal"},
                "One signal stood out today",
                cue="insight",
            ),
            scene(
                "daily-glance",
                4,
                7,
                "metric",
                "metric_grid",
                "Today at a glance",
                glance_body,
                {"category": category, "cards": cards},
                "Useful signals in context",
            ),
            scene(
                "daily-main",
                11,
                10,
                "timeline",
                "sparkline",
                f"How {primary.label.lower()} unfolded",
                primary.detail,
                payload(primary),
                "The main pattern developed across the day",
            ),
            scene(
                "daily-pattern",
                21,
                10,
                "reflection",
                "comparison" if has_pair else "data_quality",
                "Two observations, side by side"
                if has_pair
                else "A focused, single-category story",
                "They moved in the same period; that does not establish cause."
                if has_pair
                else "Only one useful category was available, so this story avoids empty comparisons.",
                {
                    "category": pair[0].category,
                    "labels": [pair[0].label, pair[1].label],
                    "values": [pair[0].value or 0, pair[1].value or 0],
                }
                if has_pair
                else {"category": category},
                "A relationship worth watching"
                if has_pair
                else "Limited data, clearly acknowledged",
                cue="insight",
            ),
            scene(
                "daily-steady",
                31,
                9,
                "metric",
                "progress_ring" if not selection.sparse else "data_quality",
                "What looked most unusual" if not selection.sparse else "What the data cannot say",
                unusual.comparison
                if not selection.sparse
                else "One category can describe a pattern, but it cannot explain why it happened.",
                payload(unusual) if not selection.sparse else {"category": category},
                "Change is context, not a diagnosis",
            ),
            scene(
                "daily-next",
                40,
                10,
                "recommendation",
                "recommendation",
                "Tomorrow’s useful experiment",
                _recommendation(category, weekly=False),
                {"category": category},
                "Try one small repeatable change",
                cue="soft",
            ),
            scene(
                "daily-close",
                50,
                10,
                "closing",
                "closing",
                "Carry forward one clear signal",
                "The goal tomorrow is context, not perfection.",
                {"category": category},
                "Small shifts become visible over time",
                cue="complete",
            ),
        ]
    else:
        scenes = [
            scene(
                "weekly-hook",
                0,
                4,
                "title",
                "hook",
                f"This week was about {primary.label.lower()}",
                primary.comparison,
                payload(primary) | {"badge": "The week’s headline"},
                "One pattern defined the week",
                cue="insight",
            ),
            scene(
                "weekly-glance",
                4,
                7,
                "metric",
                "metric_grid",
                "The week at a glance",
                glance_body,
                {"category": category, "cards": cards},
                "Headline signals",
            ),
            scene(
                "weekly-seven",
                11,
                12,
                "timeline",
                "seven_day",
                "Seven days, one visible shape",
                primary.detail,
                payload(primary),
                "The full week reveals the pattern",
            ),
            scene(
                "weekly-best",
                23,
                8,
                "metric",
                "progress_ring",
                "The clearest deviation",
                unusual.comparison,
                payload(unusual),
                "The largest change deserves context",
                cue="insight",
            ),
            scene(
                "weekly-relationship",
                31,
                10,
                "reflection",
                "comparison" if has_pair else "data_quality",
                "A relationship worth watching"
                if has_pair
                else "A focused week with limited coverage",
                "These observations coincided; neither is presented as the cause."
                if has_pair
                else "Only one useful category was available, so no relationship is inferred.",
                {
                    "category": pair[0].category,
                    "labels": [pair[0].label, pair[1].label],
                    "values": [pair[0].value or 0, pair[1].value or 0],
                }
                if has_pair
                else {"category": category},
                "Coincidence is not causation"
                if has_pair
                else "No substitute values or invented links",
            ),
            scene(
                "weekly-next",
                41,
                9,
                "recommendation",
                "recommendation",
                "Next week’s experiment",
                _recommendation(category, weekly=True),
                {"category": category},
                "Choose one repeatable experiment",
                cue="soft",
            ),
            scene(
                "weekly-close",
                50,
                10,
                "closing",
                "closing",
                "Steadiness made the story",
                "Keep the routine that was easiest to repeat.",
                {"category": category},
                "Consistency can matter more than intensity",
                cue="complete",
            ),
        ]
    categories = list(dict.fromkeys(item.category for item in selection.all_metrics))
    return Storyboard(
        title=f"Your {period_word.title()} Data Story",
        period_type=period,
        summary=f"A private {period_word} story selected locally from {discovered} available signals.",
        narration=narration,
        scenes=scenes,
        safety_notes=[
            "Direct observations only",
            "Not medical advice",
            "Personal values stay visual",
        ],
        data_categories_used=categories[:20],
    )


def _recommendation(category: str, *, weekly: bool) -> str:
    period = "next week" if weekly else "tomorrow"
    options = {
        "sleep": f"Start winding down a little earlier {period}, then watch whether the overnight pattern repeats.",
        "movement": f"Spread movement across two parts of {period} and compare the shape, not only the total.",
        "recovery": f"Keep the routine steady {period} and watch the signal without treating one reading as a conclusion.",
        "focus": f"Protect one quiet block {period}, then compare it with your usual rhythm.",
    }
    return options.get(
        category,
        f"Repeat one simple routine {period} and watch whether the pattern becomes clearer.",
    )


def generate_storyboard(
    period: PeriodType, summary: dict[str, object], config: GenerationConfig
) -> ModelResult:
    if config.provider == "offline":
        return ModelResult(personalized_storyboard(period, summary), "offline-personal", 0, 0, 0.0)
    if config.provider != "claude":
        raise ValueError("generation.provider must be claude or offline")
    from .claude_provider import ClaudeProviderError, generate_with_claude

    try:
        result = generate_with_claude(period, summary, config)
    except (ClaudeProviderError, OSError, ValueError) as exc:
        # A missing binary, a logged-out CLI, a timeout, malformed JSON or a schema
        # violation must never take the scheduler down with it.
        if not config.offline_fallback:
            raise
        return ModelResult(
            personalized_storyboard(period, summary),
            "offline-personal",
            0,
            0,
            0.0,
            redact(exc)[:400],
        )
    return ModelResult(
        result.storyboard,
        result.model,
        result.input_tokens,
        result.output_tokens,
        result.estimated_cost_usd,
        result.fallback_reason,
    )
