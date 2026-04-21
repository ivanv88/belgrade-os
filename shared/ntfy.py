import requests

# Use the EXACT same string you subscribed to in the app
TOPIC = "laurent_beg_os_2026" 

def notify(message: str, title: str = "Belgrade OS", tags: str = "rocket"):
    try:
        requests.post(
            f"https://ntfy.sh/{TOPIC}",
            data=message.encode('utf-8'),
            headers={
                "Title": title,
                "Tags": tags,
                "Priority": "high"
            }
        )
        return True
    except Exception as e:
        print(f"Notification failed: {e}")
        return False
