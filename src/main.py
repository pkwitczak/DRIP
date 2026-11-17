# main.py
from config import config
from net import Net
from ota import OtaManager

print(config.wifi_ssid, config.wifi_password)
with Net(config.wifi_ssid, config.wifi_password) as net:
    net.connect()
    print("status:", net.get_status())
    # ota = OtaManager(
    #     net,
    #     config.ota_base_url,
    #     local_version=config.local_version,
    #     manifest_name=config.ota_manifest_name,
    # )

    # res = ota.check()
    # if res.error:
    #     print("OTA check failed:", res.error)
    # elif res.needs_update:
    #     print("Update available:", res.remote_version)
    #     ota.apply()
    # else:
    #     print("Up to date:", res.current_version)

    # # Tu reszta aplikacji
    
#
