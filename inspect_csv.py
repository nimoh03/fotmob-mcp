import csv, sys, collections

path = sys.argv[1]
with open(path, newline="", encoding="utf-8-sig") as f:
    rows = list(csv.reader(f))

print("HEADER:", rows[0])
print("ROWS:", len(rows) - 1)
print()

data = rows[1:]
fx = collections.OrderedDict()
for r in data:
    if len(r) < 15:
        continue
    fx[(r[1], r[3], r[4], r[7])] = 1
print("FIXTURES:", len(fx))
for k in fx:
    print("  ", k[1], "|", k[2], "|", k[3])
print()

mk = collections.Counter(r[8] for r in data if len(r) >= 15)
print("MARKETS (name: rows)")
for name, n in mk.most_common():
    print(f"  {n:6d}  {name}")
print()

seen = set()
for r in data:
    if len(r) >= 15 and r[8] not in seen and "Over/Under" not in r[8]:
        seen.add(r[8])
        print("SAMPLE:", r[8], "|", r[10], "|", r[12], "|", r[13])