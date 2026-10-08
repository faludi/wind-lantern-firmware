import ugit
import secrets
print("Starting safe firmware pull from G...")
try:
    ugit.safe_pull_all(user='faludi', repository='wind-lantern-firmware', branch=None, token=None,
                      ssid=secrets.WIFI_SSID, password=secrets.WIFI_PASSWORD, ignore=['/README.md', '/LICENSE', '/secrets.py', '/.gitignore', '/reboot_state.json'],
                      isconnected=False, reset_after=False)
    print("Safe firmware pull completed.")
except Exception as e:
    print("Safe firmware pull failed:", e)