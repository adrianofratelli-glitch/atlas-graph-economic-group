import { evidencePath } from './evidence'

const money = n => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(n ?? 0)
export default function EvidencePanel({ group, concentration, caseInfo, alerts, onPoint }) {
  if (!group) return <p className="muted">Selecione uma empresa para reunir as evidências.</p>
  const overdue = group.nodes.filter(n => n.kind === 'company' && n.vencido > 0 && !n.is_subject)
  const partial = !group.coverage?.complete
  const rows = [
    ['Grupo consultado', `${group.stats.companies} empresas · ${group.stats.elapsed_ms} ms`, partial ? 'Alcance parcial' : 'Alcance verificado'],
    ['Exposição consolidada', money(group.group_exposure.limite), 'Soma das empresas exibidas'],
    ['Concentração semântica', concentration.loading ? 'Em análise' : concentration.out?.empty ? 'Sem atividades' : concentration.out ? `${Math.round(concentration.out.dominant_block_share * 100)}% no bloco da atividade principal` : 'Indisponível', 'Sem score de risco'],
    ['Revisão transacional', caseInfo ? `${caseInfo.companies_blocked} empresas sob revisão` : 'Nenhuma revisão nesta consulta', 'Validação no servidor'],
    ['Evento de revisão', alerts.some(a => a.case_id === caseInfo?.case_id) ? 'Recebido' : 'Aguardando evento do caso', 'Change Stream'],
  ]
  return <>
    <h2 className="evidence-title">O que esta consulta revela</h2>
    <p className={`notice ${partial ? 'notice-warn' : 'notice-ok'}`}>
      {partial ? 'Consulta parcial. Amplie os níveis; a revisão fica bloqueada enquanto o alcance não for verificado.' : 'Alcance verificado dentro do modelo societário desta POV.'}
    </p>
    <p className="small">A solicitante tem {money(group.subject.vencido)} vencidos. No conjunto exibido, são <b>{money(group.group_exposure.vencido)}</b>.</p>
    {overdue.length > 0 && <button className="btn btn-ghost btn-block" onClick={() => onPoint(evidencePath(group, overdue.map(n => n.id)))}>
      Mostrar vínculos com {overdue.length} empresas com atraso
    </button>}
    <p className="muted small">Resumo calculado a partir dos dados sintéticos. Vínculo societário não implica responsabilidade solidária ou decisão de crédito.</p>
    <ol className="evidence-timeline" aria-label="Evidências da consulta">
      {rows.map(([title, value, note]) => <li key={title}><strong>{title}</strong><span>{value}</span><small>{note}</small></li>)}
    </ol>
  </>
}
