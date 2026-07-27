import requests
import re
import json
import os

URL = (
    "https://zenless-zone-zero.fandom.com/api.php"
    "?action=parse"
    "&page=Redemption_Code"
    "&prop=wikitext"
    "&format=json"
)

FILE = "codes.json"

# Get wiki page
response = requests.get(URL)
response.raise_for_status()

data = response.json()

text = data["parse"]["wikitext"]["*"]

# Find codes
codes = re.findall(
    r"\{\{Redemption Code Row\|([^|]+)",
    text
)

# Remove header if found
codes = [
    code.strip()
    for code in codes
    if code.lower().strip() != "code"
]

# Create output
output = []

for code in codes:
    output.append({
        "code": code
    })

print("Found codes:")
print(output)


# -------------------------
# NEW CODE DETECTOR
# -------------------------

if os.path.exists(FILE):
    with open(FILE, "r", encoding="utf-8") as file:
        old_codes = json.load(file)
else:
    old_codes = []


old_code_names = [
    item["code"]
    for item in old_codes
]


new_codes = [
    item
    for item in output
    if item["code"] not in old_code_names
]


if new_codes:
    print("\nNew codes found:")
    for code in new_codes:
        print("-", code["code"])
else:
    print("\nNo new codes found.")


# Save updated codes
with open(FILE, "w", encoding="utf-8") as file:
    json.dump(output, file, indent=4)


print(f"\nSaved {len(output)} codes")