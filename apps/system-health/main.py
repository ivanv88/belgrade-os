import psutil
from shared.ntfy import notify

def check_pi_health():
    # Get CPU temperature
    # Note: On some Pi OS versions, you might need: 
    # temp = psutil.sensors_temperatures()['cpu_thermal'][0].current
    # For now, we'll just check CPU Load:
    load = psutil.cpu_percent(interval=1)
    
    if load > 80:
        notify(f"⚠️ High CPU Load: {load}%", title="System Health", tags="warning")
    
    return {"cpu_load": load, "status": "nominal"}
