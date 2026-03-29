"""Sensor platform for Vwala Open Energie distribution cost and excise duty data."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_METER_TYPE,
    CONF_TARIFF_TYPE,
    CONF_PROVIDER_ID,
    CONF_PROVIDER_NAME,
    METER_TYPE_DIGITAL,
    TARIFF_TYPE_NACHTMETER,
    LABEL_KEYWORD_CAPACITY,
    LABEL_KEYWORD_KWH,
    LABEL_KEYWORD_DATA,
    LABEL_KEYWORD_BEHEER,
    LABEL_KEYWORD_NACHT,
    LABEL_EXCISE_ENERGIEFONDS,
    LABEL_EXCISE_ACCIJNS,
    LABEL_EXCISE_ENERGIEBIJDRAGE,
    DATA_KEY_DISTRIBUTION,
    DATA_KEY_EXCISE,
)
from .coordinator import VwalaDistributionCoordinator, VwalaExciseDutiesCoordinator

_LOGGER = logging.getLogger(__name__)

# Excise duty label keywords we explicitly surface as sensors
_EXCISE_LABEL_KEYWORDS = (
    LABEL_EXCISE_ENERGIEFONDS,
    LABEL_EXCISE_ACCIJNS,
    LABEL_EXCISE_ENERGIEBIJDRAGE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _icon_for_label(label: str) -> str:
    """Return an appropriate MDI icon based on tariff label keywords."""
    low = label.lower()
    if LABEL_KEYWORD_CAPACITY in low:
        return "mdi:transmission-tower"
    if LABEL_KEYWORD_KWH in low.replace(" ", "").replace("-", ""):
        return "mdi:lightning-bolt"
    if LABEL_KEYWORD_DATA in low or LABEL_KEYWORD_BEHEER in low:
        return "mdi:database-cog"
    if any(kw in low for kw in (LABEL_EXCISE_ACCIJNS, LABEL_EXCISE_ENERGIEFONDS, LABEL_EXCISE_ENERGIEBIJDRAGE)):
        return "mdi:receipt-text"
    return "mdi:currency-eur"


def _is_kwh_item(label: str) -> bool:
    """True if this distribution cost line item represents a kWh tariff."""
    return LABEL_KEYWORD_KWH in label.lower().replace(" ", "").replace("-", "")


def _is_kwh_nacht_item(label: str) -> bool:
    """True if the kWh item is the night-exclusive variant."""
    low = label.lower()
    return _is_kwh_item(label) and (LABEL_KEYWORD_NACHT in low or "excl" in low)


def _include_kwh_item(label: str, tariff_type: str) -> bool:
    """Whether this kWh line item should be shown for the configured tariff type."""
    if tariff_type == TARIFF_TYPE_NACHTMETER:
        return _is_kwh_nacht_item(label)
    return _is_kwh_item(label) and not _is_kwh_nacht_item(label)


def _excise_rate(item: dict[str, Any]) -> float | None:
    """Extract the primary display rate from an excise duty item.

    - flat       → ``rate`` field
    - tiered     → first tier's ``rate``
    - categorized → residential category ``rate``
    """
    rate_type = item.get("rateType", "flat")
    if rate_type == "flat":
        return item.get("rate")
    if rate_type == "categorized":
        for field in ("categories", "data", "rates"):
            cats = item.get(field)
            if cats and isinstance(cats, list):
                for cat in cats:
                    if str(cat.get("category", "")).lower() == "residential":
                        return cat.get("rate")
            elif cats and isinstance(cats, dict):
                return cats.get("residential")
        return None
    # tiered – try common field names
    for field in ("tiers", "rates", "bands"):
        tiers = item.get(field)
        if tiers and isinstance(tiers, list) and len(tiers) > 0:
            return tiers[0].get("rate")
    return None


def _excise_extra(item: dict[str, Any]) -> tuple[str, list[dict]] | None:
    """Return ``(attribute_key, data_list)`` for non-flat excise items, or None.

    Used to populate extra_state_attributes with full tier / category data.
    """
    rate_type = item.get("rateType", "flat")
    if rate_type == "flat":
        return None
    if rate_type == "categorized":
        for field in ("categories", "data"):
            cats = item.get(field)
            if cats and isinstance(cats, list):
                return ("categories", cats)
        return None
    # tiered
    for field in ("tiers", "rates", "bands"):
        tiers = item.get(field)
        if tiers and isinstance(tiers, list):
            return ("tiers", tiers)
    return None


def _device_info(provider_id: str, provider_name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, provider_id)},
        name=provider_name,
        manufacturer="Vwala",
        model="Distributienetbeheerder",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://open-energie.docs.vwala.be",
    )


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up all Vwala sensors from a config entry."""
    entry_data: dict = hass.data[DOMAIN][entry.entry_id]
    distribution_coordinator: VwalaDistributionCoordinator = entry_data[DATA_KEY_DISTRIBUTION]
    excise_coordinator: VwalaExciseDutiesCoordinator = entry_data[DATA_KEY_EXCISE]

    meter_type: str = entry.data[CONF_METER_TYPE]
    tariff_type: str = entry.data[CONF_TARIFF_TYPE]
    provider_id: str = entry.data[CONF_PROVIDER_ID]
    provider_name: str = entry.data[CONF_PROVIDER_NAME]

    entities: list[SensorEntity] = []

    # ---- Distribution cost sensors ----------------------------------------
    cost_data: list[dict[str, Any]] = (
        distribution_coordinator.data.get("data", [])
        if distribution_coordinator.data
        else []
    )

    if not cost_data:
        _LOGGER.warning(
            "No distribution cost data available for provider %s; "
            "sensors will be created but may show unavailable until next refresh.",
            provider_id,
        )

    for item in cost_data:
        label: str = item.get("label", item.get("id", ""))

        if _is_kwh_item(label):
            # Only include if it matches the configured tariff type
            if not _include_kwh_item(label, tariff_type):
                _LOGGER.debug(
                    "Skipping kWh tariff item '%s' for tariff_type '%s'",
                    label,
                    tariff_type,
                )
                continue

            # Primary sensor: Nettarieven (MWh) – raw API value
            entities.append(
                VwalaDistributionCostSensor(
                    coordinator=distribution_coordinator,
                    item=item,
                    meter_type=meter_type,
                    provider_id=provider_id,
                    provider_name=provider_name,
                    override_name="Nettarieven (MWh)",
                )
            )
            # Derived sensor: Nettarieven (kWh) = value / 1000
            entities.append(
                VwalaNetTariffKwhSensor(
                    coordinator=distribution_coordinator,
                    item=item,
                    meter_type=meter_type,
                    provider_id=provider_id,
                    provider_name=provider_name,
                )
            )
            _LOGGER.debug("Registering Nettarieven sensors for item '%s'", label)
        else:
            entities.append(
                VwalaDistributionCostSensor(
                    coordinator=distribution_coordinator,
                    item=item,
                    meter_type=meter_type,
                    provider_id=provider_id,
                    provider_name=provider_name,
                )
            )
            _LOGGER.debug("Registering sensor for tariff item '%s'", label)

    # ---- Excise duty sensors -----------------------------------------------
    excise_data: list[dict[str, Any]] = (
        excise_coordinator.data.get("data", [])
        if excise_coordinator.data
        else []
    )

    for item in excise_data:
        # Strip hyphens so hyphenated IDs like "bijdrage-energie-fonds" still
        # match keyword "fonds", and "federale-energie-bijdrage" matches "energiebijdrage".
        label_low = item.get("label", item.get("id", "")).lower().replace("-", "")
        if any(kw in label_low for kw in _EXCISE_LABEL_KEYWORDS):
            entities.append(
                VwalaExciseDutySensor(
                    coordinator=excise_coordinator,
                    item=item,
                    provider_id=provider_id,
                    provider_name=provider_name,
                )
            )
            _LOGGER.debug("Registering excise duty sensor for item '%s'", item.get("label"))

    async_add_entities(entities, update_before_add=False)


# ---------------------------------------------------------------------------
# Distribution cost sensor
# ---------------------------------------------------------------------------

class VwalaDistributionCostSensor(
    CoordinatorEntity[VwalaDistributionCoordinator], SensorEntity
):
    """A single tariff line-item sensor on the distribution net provider device.

    One sensor is created per entry in the ``data`` array returned by:
    ``GET /v1/distribution-net-providers/{id}/distribution-costs/electricity``

    The sensor value is chosen based on the user's ``meter_type``:
    - Digital meter  → ``values.withDigitalMeter``
    - Analog meter   → ``values.withAnalogMeter``
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: VwalaDistributionCoordinator,
        item: dict[str, Any],
        meter_type: str,
        provider_id: str,
        provider_name: str,
        override_name: str | None = None,
    ) -> None:
        super().__init__(coordinator)

        self._item_id: str = item["id"]
        self._api_label: str = item.get("label", item["id"])
        self._unit: str = item.get("unit", "€/jaar")
        self._meter_type = meter_type
        self._provider_id = provider_id
        self._provider_name = provider_name

        display_name = override_name or self._api_label
        suffix = "_mwh" if override_name else ""
        self._attr_unique_id = f"{provider_id}_{self._item_id}{suffix}"
        self._attr_name = display_name
        self._attr_icon = _icon_for_label(self._api_label)
        self._attr_native_unit_of_measurement = self._unit

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._provider_id, self._provider_name)

    def _current_item(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        for item in self.coordinator.data.get("data", []):
            if item["id"] == self._item_id:
                return item
        return None

    @property
    def native_value(self) -> float | None:
        """Return the tariff value for the configured meter type."""
        item = self._current_item()
        if item is None:
            return None
        values = item.get("values", {})
        if isinstance(values, dict):
            if self._meter_type == METER_TYPE_DIGITAL:
                return values.get("withDigitalMeter")
            return values.get("withAnalogMeter")
        if isinstance(values, (int, float)):
            return values
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose both meter-type values and original API label for advanced users."""
        item = self._current_item()
        if item is None:
            return {}
        values = item.get("values", {})
        attrs: dict[str, Any] = {
            "tariff_id": self._item_id,
            "api_label": self._api_label,
            "unit": self._unit,
        }
        if isinstance(values, dict):
            attrs["with_digital_meter"] = values.get("withDigitalMeter")
            attrs["with_analog_meter"] = values.get("withAnalogMeter")
        return attrs


# ---------------------------------------------------------------------------
# Derived kWh sensor (Nettarieven MWh → kWh)
# ---------------------------------------------------------------------------

class VwalaNetTariffKwhSensor(VwalaDistributionCostSensor):
    """Derived sensor that converts the MWh net tariff to €/kWh by dividing by 1000."""

    def __init__(
        self,
        coordinator: VwalaDistributionCoordinator,
        item: dict[str, Any],
        meter_type: str,
        provider_id: str,
        provider_name: str,
    ) -> None:
        super().__init__(
            coordinator=coordinator,
            item=item,
            meter_type=meter_type,
            provider_id=provider_id,
            provider_name=provider_name,
            override_name="Nettarieven (kWh)",
        )
        # Override unique_id and unit to avoid clash with the MWh sensor
        self._attr_unique_id = f"{provider_id}_{item['id']}_kwh"
        self._attr_native_unit_of_measurement = "€/kWh"
        self._attr_icon = "mdi:lightning-bolt"

    @property
    def native_value(self) -> float | None:
        mwh_value = super().native_value
        if mwh_value is None:
            return None
        return round(mwh_value / 1000, 8)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = super().extra_state_attributes
        # Also expose the converted values for both meter types
        if attrs.get("with_digital_meter") is not None:
            attrs["with_digital_meter_kwh"] = round(attrs["with_digital_meter"] / 1000, 8)
        if attrs.get("with_analog_meter") is not None:
            attrs["with_analog_meter_kwh"] = round(attrs["with_analog_meter"] / 1000, 8)
        attrs["unit"] = "€/kWh"
        return attrs


# ---------------------------------------------------------------------------
# Excise duty sensor
# ---------------------------------------------------------------------------

class VwalaExciseDutySensor(
    CoordinatorEntity[VwalaExciseDutiesCoordinator], SensorEntity
):
    """Sensor for a single electricity excise duty / levy.

    For tiered items (e.g. federale bijzondere accijns) the ``native_value``
    is the **first tier** rate; all tiers are stored in ``extra_state_attributes``.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: VwalaExciseDutiesCoordinator,
        item: dict[str, Any],
        provider_id: str,
        provider_name: str,
    ) -> None:
        super().__init__(coordinator)

        self._item_id: str = item["id"]
        self._label: str = item.get("label", item["id"])
        self._unit: str = item.get("unit", "€/kWh")
        self._provider_id = provider_id
        self._provider_name = provider_name

        self._attr_unique_id = f"{provider_id}_excise_{self._item_id}"
        self._attr_name = self._label
        self._attr_icon = _icon_for_label(self._label)
        self._attr_native_unit_of_measurement = self._unit

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._provider_id, self._provider_name)

    def _current_item(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        for item in self.coordinator.data.get("data", []):
            if item["id"] == self._item_id:
                return item
        return None

    @property
    def native_value(self) -> float | None:
        item = self._current_item()
        if item is None:
            return None
        return _excise_rate(item)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = self._current_item()
        if item is None:
            return {}

        rate_type = item.get("rateType", "flat")
        attrs: dict[str, Any] = {
            "excise_id": self._item_id,
            "rate_type": rate_type,
            "unit": self._unit,
        }

        extra = _excise_extra(item)
        if extra is not None:
            key, data = extra
            attrs[key] = data
            if rate_type == "categorized":
                attrs["note"] = "native_value shows residential category rate"
            else:
                attrs["note"] = "native_value shows first tier rate"

        return attrs
