from app.env_reader import env
from app.version import VERSION as LOCAL_VERSION

class Config:
    def __init__(self, env_reader):
        with env_reader:
            self.wifi_ssid = env_reader.get_required("WIFI_SSID")
            self.wifi_password = env_reader.get_required("WIFI_PASSWORD")
            self.ota_base_url = env_reader.get_required("OTA_BASE_URL")
            self.local_version = LOCAL_VERSION
            self.ota_manifest_name = env_reader.get("OTA_MANIFEST_NAME", "manifest.json")


config = Config(env)
