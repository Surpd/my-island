import React, { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import './preview.css'

export const cohorts = {
  junior:{ grade:'5–6 классы', classroom:'5Б', subject:'Математика' },
  middle:{ grade:'7–9 классы', classroom:'8Б', subject:'Алгебра' },
  senior:{ grade:'10 класс', classroom:'10А', subject:'Алгебра и начала анализа' },
  graduate:{ grade:'11 класс', classroom:'11А', subject:'Алгебра и начала анализа' },
}

const dataOptions = [
  ['ready','Загружены'],
  ['loading','Загрузка'],
  ['empty','Пустой ответ'],
  ['unpublished','Расписание не опубликовано'],
  ['error','Ошибка'],
]
const defaultScenario = { role:'student', stage:'middle', homeroom:true, state:'ready' }

export function getPreview() {
  const url = new URL(location.href)
  const { searchParams:params } = url
  if (document.body.dataset.campusEntry !== 'preview' && params.get('preview') !== '1') {
    return { scenario:defaultScenario }
  }
  const scenario = {
    role:params.get('role') === 'teacher' ? 'teacher' : defaultScenario.role,
    stage:Object.hasOwn(cohorts,params.get('stage')) ? params.get('stage') : defaultScenario.stage,
    homeroom:params.get('homeroom') !== '0',
    state:dataOptions.some(([value]) => value === params.get('state')) ? params.get('state') : defaultScenario.state,
  }
  const showForm = params.get('campus') !== '1'
  params.delete('campus')
  return { scenario, showForm, toolsHref:url.href }
}

export default function Preview({ scenario, theme, largeText, viewport }) {
  const [selected,setSelected] = useState(scenario)
  const teacher = selected.role === 'teacher'
  const change = (key,value) => setSelected(current => ({ ...current, [key]:value }))
  function launch(event) {
    event.preventDefault()
    const target = new URL(location.href)
    target.searchParams.set('campus','1')
    for (const [name,value] of Object.entries(selected)) {
      target.searchParams.set(name,typeof value === 'boolean' ? Number(value) : value)
    }
    location.assign(target)
  }
  return <div className="campus" data-theme={theme} data-text={largeText ? 'large' : undefined} style={viewport}>
    <main className="preview-screen">
      <h1>Предпросмотр кампуса</h1>
      <p>Выберите роль и состояние вымышленных школьных данных.</p>
      <form onSubmit={launch}>
        <label className="field"><span>Роль</span><select id="role" value={selected.role} onChange={e=>change('role',e.target.value)}>
          <option value="student">Ученик</option><option value="teacher">Учитель</option>
        </select></label>
        {!teacher && <label className="field"><span>Возрастной кампус</span><select id="age-group" value={selected.stage} onChange={e=>change('stage',e.target.value)}>
          {Object.entries(cohorts).map(([id,cohort])=><option key={id} value={id}>{cohort.grade}</option>)}
        </select></label>}
        {teacher && <label className="check">Классное руководство<input type="checkbox" id="homeroom" checked={selected.homeroom} onChange={e=>change('homeroom',e.target.checked)}/></label>}
        <label className="field"><span>Данные в локациях</span><select id="data-state" value={selected.state} onChange={e=>change('state',e.target.value)}>
          {dataOptions.map(([value,name])=><option key={value} value={value}>{name}</option>)}
        </select></label>
        <button type="submit" id="open-preview-campus">Открыть кампус <ArrowRight size={19} aria-hidden="true"/></button>
      </form>
    </main>
  </div>
}
