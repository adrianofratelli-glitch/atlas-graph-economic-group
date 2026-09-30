// Caminho não dirigido: explica vínculos, sem inferir controle ou causalidade.
export function evidencePath(group, targetIds) {
  if (!group?.subject) return []
  const adjacent = new Map()
  for (const edge of group.edges ?? []) {
    for (const [a, b] of [[edge.from, edge.to], [edge.to, edge.from]]) {
      if (!adjacent.has(a)) adjacent.set(a, [])
      adjacent.get(a).push(b)
    }
  }
  const root = group.subject.id
  const previous = new Map([[root, null]])
  const queue = [root]
  for (let i = 0; i < queue.length; i++) {
    for (const id of adjacent.get(queue[i]) ?? []) {
      if (!previous.has(id)) { previous.set(id, queue[i]); queue.push(id) }
    }
  }
  const result = new Set()
  for (const target of targetIds) {
    if (!previous.has(target)) continue
    for (let id = target; id != null; id = previous.get(id)) result.add(id)
  }
  return [...result]
}
