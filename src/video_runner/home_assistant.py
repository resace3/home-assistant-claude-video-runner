from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

SENSOR_PREFIXES = ("sensor.", "binary_sensor.")
EXPLICIT_PREFIXES = (*SENSOR_PREFIXES, "input_number.")


@dataclass(frozen=True)
class SensorSnapshot:
    entity_id: str
    name: str
    state: str
    unit: str
    device_class: str


class HomeAssistantClient:
    def __init__(
        self, token: str | None = None, base_url: str = "http://supervisor/core/api"
    ) -> None:
        self._token = token or os.environ.get("SUPERVISOR_TOKEN", "")
        if not self._token:
            raise RuntimeError("SUPERVISOR_TOKEN is unavailable in this supervised runtime")
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=httpx.Timeout(15),
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(4):
            try:
                response = self._client.get(path, params=params)
                last_response = response
                if response.status_code not in {429, 500, 502, 503, 504}:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise RuntimeError(
                            f"Home Assistant API request failed with HTTP {exc.response.status_code}"
                        ) from None
                    return response
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 3:
                    raise
            time.sleep(min(4.0, 0.5 * (2**attempt)))
        assert last_response is not None
        try:
            last_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Home Assistant API request failed with HTTP {exc.response.status_code}"
            ) from None
        return last_response

    def fetch_sensor_snapshots(
        self,
        entity_ids: Iterable[str] | None = None,
        *,
        include_binary_sensors: bool = True,
        max_entities: int = 2_000,
        max_response_bytes: int = 10_000_000,
    ) -> dict[str, SensorSnapshot]:
        """Read current metadata for every automatic sensor or an explicit safe list."""
        selected = set(entity_ids) if entity_ids is not None else None
        if selected is not None:
            if not selected:
                return {}
            if any(not entity_id.startswith(EXPLICIT_PREFIXES) for entity_id in selected):
                raise ValueError("entity allowlist contains a disallowed domain")
        response = self._get("/states")
        if len(response.content) > max_response_bytes:
            raise RuntimeError("Home Assistant states response exceeded the configured cap")
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Home Assistant states response was not a list")
        automatic_prefixes = SENSOR_PREFIXES if include_binary_sensors else ("sensor.",)
        snapshots: dict[str, SensorSnapshot] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            entity_id = item.get("entity_id")
            if not isinstance(entity_id, str):
                continue
            if selected is None:
                if not entity_id.startswith(automatic_prefixes):
                    continue
            elif entity_id not in selected:
                continue
            attributes = item.get("attributes")
            safe_attributes = attributes if isinstance(attributes, dict) else {}
            friendly_name = safe_attributes.get("friendly_name")
            state = item.get("state")
            snapshots[entity_id] = SensorSnapshot(
                entity_id=entity_id,
                name=(
                    friendly_name
                    if isinstance(friendly_name, str) and friendly_name.strip()
                    else entity_id.split(".", 1)[-1].replace("_", " ").title()
                ),
                state=str(state) if state is not None else "unknown",
                unit=(
                    str(safe_attributes.get("unit_of_measurement"))
                    if safe_attributes.get("unit_of_measurement") is not None
                    else ""
                ),
                device_class=(
                    str(safe_attributes.get("device_class"))
                    if safe_attributes.get("device_class") is not None
                    else ""
                ),
            )
        if selected is None and len(snapshots) > max_entities:
            raise RuntimeError(
                f"Automatic sensor discovery found {len(snapshots)} entities, above the "
                f"configured safety cap of {max_entities}"
            )
        return dict(sorted(snapshots.items()))

    def fetch_allowlisted_history(
        self,
        entity_ids: Iterable[str],
        *,
        period: str,
        daily_hours: int,
        weekly_days: int,
        max_observations_per_entity: int = 512,
        max_response_bytes: int = 2_000_000,
        batch_size: int = 20,
    ) -> dict[str, list[object]]:
        allowed = list(dict.fromkeys(entity_ids))
        if not allowed:
            return {}
        for entity_id in allowed:
            if not entity_id.startswith(EXPLICIT_PREFIXES):
                raise ValueError("entity allowlist contains a disallowed domain")
        if not 1 <= batch_size <= 100:
            raise ValueError("history batch size must be between 1 and 100")
        delta = timedelta(hours=daily_hours) if period == "daily" else timedelta(days=weekly_days)
        start = (datetime.now(UTC) - delta).isoformat()
        result: dict[str, list[object]] = {entity_id: [] for entity_id in allowed}
        batches = [
            allowed[index : index + batch_size] for index in range(0, len(allowed), batch_size)
        ]
        while batches:
            batch = batches.pop(0)
            response = self._get(
                f"/history/period/{start}",
                params={
                    "filter_entity_id": ",".join(batch),
                    "minimal_response": "true",
                    "no_attributes": "true",
                },
            )
            if len(response.content) > max_response_bytes:
                if len(batch) == 1:
                    raise RuntimeError(
                        "Home Assistant history response for one entity exceeded the configured cap"
                    )
                midpoint = len(batch) // 2
                batches[0:0] = [batch[:midpoint], batch[midpoint:]]
                continue
            for series in response.json():
                if not isinstance(series, list) or not series or not isinstance(series[0], dict):
                    continue
                series_entity = series[0].get("entity_id")
                if not isinstance(series_entity, str) or series_entity not in result:
                    continue
                states = [sample.get("state") for sample in series if isinstance(sample, dict)]
                if len(states) > max_observations_per_entity:
                    stride = max(1, len(states) // (max_observations_per_entity - 1))
                    states = states[::stride][: (max_observations_per_entity - 1)] + [states[-1]]
                result[series_entity] = states
        return result

    def __enter__(self) -> HomeAssistantClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
