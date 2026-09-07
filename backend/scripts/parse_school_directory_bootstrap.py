import json, re, sys
from pathlib import Path

raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
def clean(v):
    return re.sub(r"\s+", " ", str(v or "").replace("\u00a0", " ")).strip()
def strip_note(v):
    return clean(re.sub(r"\s*\([^)]*\)\s*$", "", str(v or "")))
def key(v):
    return re.sub(r"[^a-zа-яё0-9]+", "", clean(v).casefold().replace("ё", "е"), flags=re.I)
def letter(i):
    s=""
    while i >= 0:
        s=chr(i%26+65)+s
        i=i//26-1
    return s

people={}
groups={}
members={}
teacher_hints={}
def add_person(name, cls=None, ref=""):
    name=strip_note(name)
    if not name or re.fullmatch(r"(?:true|false|\d+)", name, re.I): return ""
    k=key(name)
    parts=name.split(" ",1)
    aliases={"катя":"екатерина","оля":"ольга","саша":"александра","вика":"виктория","миша":"михаил","петя":"петр","леша":"алексей","лёша":"алексей","артём":"артем","маша":"мария","тая":"таисия","вася":"василий"}
    if k not in people and len(parts)==2 and parts[1].casefold() in aliases:
        surname, given=parts
        matches=[pk for pk,p in people.items() if str(p["name"]).split(" ",1)[0].casefold()==surname.casefold() and key(str(p["name"]).split(" ",1)[1])==key(aliases[given.casefold()])]
        if len(matches)==1: return matches[0]
    if k not in people: people[k]={"name":name,"class_name":cls,"source_ref":ref}
    elif cls and not people[k].get("class_name"): people[k]["class_name"]=cls
    return k
def add_group(g):
    groups.setdefault(g["key"], g)
def add_member(name,gkey,ref,cls=None,note=""):
    pk=add_person(name,cls,ref)
    if pk and gkey in groups: members.setdefault((pk,gkey),{"person":pk,"group":gkey,"source_ref":ref,"note":note})

base=raw["base"]
for col,cls in ((0,"5"),(2,"6"),(4,"7"),(6,"8"),(8,"9-Д"),(10,"9-А"),(12,"10"),(15,"11")):
    gkey="class:"+cls
    add_group({"key":gkey,"name":cls,"group_type":"class","display_name":cls+" · базовый класс","subject":None,"base_class_name":cls,"subject_subgroup":None,"exam_track":None,"source_ref":"Списки по классам 26/27!"+letter(col+1)+"2:"+letter(col+1)+"100"})
    for r in range(1,len(base)):
        n=strip_note(base[r][col+1] if col+1<len(base[r]) else "")
        if n and not re.fullmatch(r"(?:true|false|\d+)",n,re.I): add_member(n,gkey,"Списки по классам 26/27!"+letter(col+1)+str(r+1),cls)

tab=raw["groups"]
def roster(subject,cr,lr,cols,end_row):
    for col in cols:
        label=clean(tab[lr][col] if col<len(tab[lr]) else "")
        cls=clean(tab[cr][col] if col<len(tab[cr]) else "")
        # Google Sheets uses merged headings: only the first column of a
        # multi-column block contains the grade. Carry it to adjacent groups.
        if not cls:
            for previous in reversed(cols):
                if previous >= col: continue
                candidate=clean(tab[cr][previous] if previous<len(tab[cr]) else "")
                if candidate:
                    cls=candidate
                    break
        if not cls and subject == "Математика":
            match=re.match(r"^9-", label, re.I)
            if match: cls="9 класс"
        if not cls or not label: continue
        mm=re.match(r"^9-([ABC])",label,re.I) if subject=="Математика" else None
        if mm:
            sub=mm.group(1).upper(); gkey="math9:"+sub
            g={"key":gkey,"name":"grade9-math-"+sub,"group_type":"subject_group","display_name":"Математика · группа "+sub+" · 9 класс","subject":"Математика","base_class_name":None,"subject_subgroup":sub,"exam_track":None,"source_ref":"списки групп 26-27!"+letter(col)+str(lr+1)}
        else:
            exam=bool(re.search(r"ОГЭ|ЕГЭ",label,re.I)); gkey="subject:"+subject+":"+cls+":"+label
            g={"key":gkey,"name":gkey,"group_type":"exam_track" if exam else "subject_group","display_name":subject+" · "+cls+" · "+label,"subject":subject,"base_class_name":cls.replace(" класс",""),"subject_subgroup":None if exam else label,"exam_track":"ОГЭ" if "ОГЭ" in label else "ЕГЭ" if "ЕГЭ" in label else None,"source_ref":"списки групп 26-27!"+letter(col)+str(lr+1)}
        add_group(g)
        hint=re.sub(r"^[0-9]+\s*","",label).strip()
        if hint: teacher_hints.setdefault(hint,[]).append(g["source_ref"])
        for r in range(lr+1,min(end_row,len(tab))):
            n=clean(tab[r][col] if col<len(tab[r]) else "")
            roster_cls=cls.replace(" класс","")
            if roster_cls == "9": roster_cls=None
            if n: add_member(n,gkey,"списки групп 26-27!"+letter(col)+str(r+1),roster_cls)
roster("Математика",3,4,[1,3,5,7,9,11,13,15,17,19,21],17)
roster("Английский язык",24,25,[1,3,5,7,9,11,13,15,17,19],41)
roster("Обществознание",48,49,[1,3],66)
roster("Литература",48,49,[13,15,17],66)
roster("География",48,49,[19],66)
roster("Биология",70,71,[1,3,5],79)
roster("Физика",70,71,[7,9,11],79)
roster("Химия",70,71,[13,15],79)
roster("История",85,86,[1,3,5],98)
roster("Информатика",85,86,[9,11,13,15],98)

exam=raw["exam"]
for ri,row in enumerate(exam):
    sec=clean(row[1] if len(row)>1 else "")
    mm=re.fullmatch(r"(\d+) класс",sec,re.I)
    if not mm or ri+1>=len(exam): continue
    grade=mm.group(1); header=exam[ri+1]
    for rr in range(ri+2,len(exam)):
        vals=exam[rr]; pn=clean(vals[1] if len(vals)>1 else "")
        if not pn or re.fullmatch(r"\d+ класс",pn,re.I): break
        for col in range(2,min(len(header),len(vals))):
            val=clean(vals[col]); subj=clean(header[col] if col<len(header) else "")
            if not subj or not val or val.casefold() in ("false","просто"): continue
            gkey="exam:"+grade+":"+subj
            add_group({"key":gkey,"name":gkey,"group_type":"exam_track","display_name":grade+" класс · "+subj,"subject":subj,"base_class_name":grade,"subject_subgroup":None,"exam_track":"ОГЭ" if grade=="9" else "ЕГЭ","source_ref":"ОГЭ/ЕГЭ!"+letter(col)+str(rr+1)})
            add_member(pn,gkey,"ОГЭ/ЕГЭ!"+letter(col)+str(rr+1),None,val)
        mg=clean(vals[13] if len(vals)>13 else "")
        if grade=="9" and re.fullmatch(r"[ABC]",mg,re.I): add_member(pn,"math9:"+mg.upper(),"ОГЭ/ЕГЭ!N"+str(rr+1))

issues=[]
if teacher_hints:
    issues.append({"issue_type":"ambiguous_teacher_headers","natural_key":"groups:teacher-hints","details":{"message":"Вкладка групп содержит teacher hints/имена без надёжной полной identity; teaching assignments не выводились автоматически.","hints":sorted(teacher_hints),"source_refs":sorted({x for xs in teacher_hints.values() for x in xs})}})
unknown=sorted(p["name"] for p in people.values() if not p.get("class_name"))
if unknown:
    issues.append({"issue_type":"students_without_base_class","natural_key":"students:missing-base-class","details":{"message":"Экзаменационный источник содержит учеников, которых нельзя однозначно связать с базовой колонкой.","people":unknown,"source":"ОГЭ/ЕГЭ"}})

people_list=[dict(value,key=person_key) for person_key,value in people.items()]
struct={"people":people_list,"groups":list(groups.values()),"memberships":list(members.values())}
records=[]
for typ,vals in (("person",struct["people"]),("group",struct["groups"]),("membership",struct["memberships"])):
    for v in vals:
        nk=("membership:"+v["person"]+":"+v["group"]) if typ=="membership" else typ+":"+str(v.get("name") or v.get("key"))
        records.append({"record_key":nk,"source_ref":v.get("source_ref","google:school-directory"),"parse_status":"validated","structural_payload":v})
for issue in issues: records.append({"record_key":"issue:"+issue["natural_key"],"source_ref":"ОГЭ/ЕГЭ; списки групп 26-27","parse_status":"unresolved","structural_payload":issue})
candidates=[]
for v in struct["people"]: candidates.append({"entity_type":"person","change_type":"create","natural_key":"person:"+v["name"],"payload":v,"evidence":{"source_ref":v.get("source_ref","google:school-directory")}})
for v in struct["groups"]: candidates.append({"entity_type":"group","change_type":"create","natural_key":"group:"+v["name"]+":"+v["group_type"],"payload":v,"evidence":{"source_ref":v.get("source_ref","google:school-directory")}})
for v in struct["memberships"]: candidates.append({"entity_type":"membership","change_type":"create","natural_key":"membership:"+v["person"]+":"+v["group"],"payload":v,"evidence":{"source_ref":v["source_ref"]}})
print(json.dumps({"raw":raw,"structural":struct,"records":records,"candidates":candidates,"issues":issues},ensure_ascii=False,separators=(",",":")))
