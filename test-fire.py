import requests

# Make sure this matches what you typed in the Android app!
TOPIC = "laurent_beg_os_2026" 

def test_push():
    print(f"🚀 Sending test notification to topic: {TOPIC}...")
    try:
        response = requests.post(
            f"https://ntfy.sh/{TOPIC}",
            data="Zdravo! If you see this, the Belgrade OS bridge is alive. 🚀".encode('utf-8'),
            headers={
                "Title": "OS Connectivity Test",
                "Priority": "high",
                "Tags": "tada,serbia"
            }
        )
        if response.status_code == 200:
            print("✅ Success! Check your phone.")
        else:
            print(f"❌ Failed with status code: {response.status_code}")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_push()
