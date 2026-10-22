import fs from 'node:fs/promises';
const source=JSON.parse(await fs.readFile('artifacts/template-restore/recovered-draft-payload.json','utf8'));
const classes=source.classes.map((id,i)=>{const labels=source.lessons.filter(l=>l.classId===id).map(l=>l.source?.source_class_label).filter(Boolean);const counts=new Map();labels.forEach(l=>counts.set(l,(counts.get(l)||0)+1));return {id,name:[...counts].sort((a,b)=>b[1]-a[1])[0]?.[0]||`Класс ${i+1}`,group_type:'class'};});
const tids=[...new Set(source.lessons.flatMap(l=>[l.teacherId,...(l.teacherIds||[])])).values()].filter(Boolean);
const gids=[...new Set(source.lessons.flatMap(l=>[l.groupId,...(l.groupIds||[])])).values()].filter(Boolean);
const directory={groups:[...classes,...gids.filter(id=>!source.classes.includes(id)).map((id,i)=>{const l=source.lessons.find(l=>l.groupId===id||l.groupIds?.includes(id));return {id,name:`${l.subject} · группа ${i+1}`,subject:l.subject,group_type:'subject_group',base_class_name:classes.find(c=>c.id===l.classId)?.name||''};})],teachers:tids.map((id,i)=>({id,name:`Преподаватель ${String(i+1).padStart(2,'0')}`,assignments:[]})),students:[],rooms:[]};
const payload=structuredClone(source);delete payload.import_review;delete payload.school_source;
payload.lessons=payload.lessons.map(l=>{const n={...l};delete n.source;delete n.audience_snapshot;return n;});
const draft={id:'audit-full-template',name:'АУДИТ · копия школьного шаблона r45',revision:45,payload};

export {directory,payload,draft};
