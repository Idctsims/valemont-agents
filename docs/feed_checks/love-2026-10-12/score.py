import re, sys

TOPIC = re.compile(r"international_sales|media_rights|format|co_production|licensing|distribution|acquisition|tv_distribution|film_sales")


def load(path):
    rows = {}
    for line in open(path, encoding="utf-8").read().splitlines()[1:]:
        p = line.split("\t")
        if len(p) < 8 or not p[0].isdigit():
            continue
        rows[int(p[1])] = dict(pos=int(p[0]), tier=p[2], topics=p[6], title=p[7])
    return rows


def page_one(rows):
    return sorted((r["pos"], i) for i, r in rows.items()
                  if r["pos"] <= 30 and r["tier"] == "NULL" and TOPIC.search((r["topics"] + " " + r["title"]).lower()))


before, after = load(sys.argv[1]), load(sys.argv[2])
H, C = 2308, 1090
hb, ha = before[H]["pos"], after.get(H, {}).get("pos")
cb, ca = before[C]["pos"], after.get(C, {}).get("pos")
print(f"held-out {H}: {hb} -> {ha}")
print(f"control  {C}: {cb} -> {ca}")
a = (hb - ha) - (cb - ca)
print(f"(a) ({hb} - {ha}) - ({cb} - {ca}) = {a}  -> {'PASS' if a >= 15 else 'FAIL'} (needs >= 15)")
p0, p1 = page_one(before), page_one(after)
print(f"(b) topic on page one {len(p0)} -> {len(p1)}  -> {'PASS' if len(p1) > 9 else 'FAIL'} (needs > 9)")
print("    before:", p0)
print("    after: ", p1)
