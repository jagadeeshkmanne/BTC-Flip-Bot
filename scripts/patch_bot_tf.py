import re

with open("/Users/jags/Desktop/BTC-Flip-Bot/bot/bot_v2_3.py", "r") as f:
    content = f.read()

# Replace df_5m with df_1h
content = content.replace('df_5m', 'df_1h')
# Replace "5m" fetch with "1h"
content = content.replace('fetch_klines("5m",', 'fetch_klines("1h",')
# Time SL calculation (300 seconds -> 3600 seconds)
content = content.replace('// 300)  # 5m bars', '// 3600)  # 1h bars')
content = content.replace('// 300)', '// 3600)')
content = content.replace('minutes=TIME_SL_BARS * 5', 'minutes=TIME_SL_BARS * 60')

# Disable the 1h cumulative move filter since it was meant for 5m bars
content = re.sub(
    r'if sig and state\["position"\] is None and len\(df_1h\) >= 14:.*?except \(KeyError, IndexError, ValueError\) as e:.*?sig = None',
    '',
    content,
    flags=re.DOTALL
)

with open("/Users/jags/Desktop/BTC-Flip-Bot/bot/bot_v2_3.py", "w") as f:
    f.write(content)
