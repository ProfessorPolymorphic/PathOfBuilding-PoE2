import type { NodeCandidate } from '../api'
import { ChangeCard } from './ChangeCard'

// A reachable passive as a change preview card, with what it does and the path to it.
export function NodeCard({ node }: { node: NodeCandidate }) {
  return (
    <ChangeCard
      card={node.card}
      title={node.name}
      subtitle={`${node.type} · ${node.cost} ${node.cost === 1 ? 'point' : 'points'}`}
    >
      <details className="cc-more">
        <summary>What it does{node.path.length > 1 ? ' and the path' : ''}</summary>
        <ul className="cc-stats">{node.stats.map((s, i) => <li key={i}>{s}</li>)}</ul>
        {node.path.length > 1 && <p className="cc-path">{node.path.join(' → ')}</p>}
      </details>
    </ChangeCard>
  )
}
