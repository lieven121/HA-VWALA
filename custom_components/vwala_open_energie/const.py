"""Constants for the Vwala Open Energie integration."""

DOMAIN = "vwala_open_energie"

BASE_URL = "https://open-energie.api.vwala.be"

# Config entry keys
CONF_EMAIL = "email"
CONF_API_KEY = "api_key"
CONF_POSTAL_CODE = "postal_code"
CONF_METER_TYPE = "meter_type"
CONF_TARIFF_TYPE = "tariff_type"
CONF_PROVIDER_ID = "provider_id"
CONF_PROVIDER_NAME = "provider_name"

# Meter type values
METER_TYPE_DIGITAL = "Digital"
METER_TYPE_ANALOG = "Analog"

# Tariff type values (maps to API tariffType param)
TARIFF_TYPE_ENKELVOUDIG = "enkelvoudig"   # Single rate (analog, no night)
TARIFF_TYPE_TWEEVOUDIG = "tweevoudig"     # Day/night (digital)
TARIFF_TYPE_NACHTMETER = "nachtmeter"     # Exclusive night meter

# Context keys used during config flow
FLOW_METHOD_ID = "method_id"
FLOW_OTP_SENT_AT = "otp_sent_at"

# Maximum seconds to wait for OTP entry (informational, shown to user)
OTP_VALIDITY_SECONDS = 120

# How often to refresh distribution cost data (tariffs change at most yearly)
UPDATE_INTERVAL_HOURS = 24

# Known label keywords for icon mapping
LABEL_KEYWORD_CAPACITY = "capaciteit"
LABEL_KEYWORD_KWH = "kwh"
LABEL_KEYWORD_DATA = "data"
LABEL_KEYWORD_BEHEER = "beheer"
# Used to distinguish the night-exclusive kWh tariff line item
LABEL_KEYWORD_NACHT = "nacht"

# Excise duty label keywords (matched case-insensitively against API label)
LABEL_EXCISE_ENERGIEFONDS = "fonds"
LABEL_EXCISE_ACCIJNS = "accijns"
LABEL_EXCISE_ENERGIEBIJDRAGE = "energiebijdrage"

# Keys used in hass.data[DOMAIN][entry_id] dict
DATA_KEY_DISTRIBUTION = "distribution"
DATA_KEY_EXCISE = "excise"

# Platforms
PLATFORMS = ["sensor"]
