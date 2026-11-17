import type { CSSProperties } from 'react';
import type { Plan } from './api/client.ts';

type Stages = Array<[number, Plan['tasks']]>

type NodePosition = { x: number; y: number; stage: number };

const NODE_WIDTH = 220;
const COLUMN_GAP = 54;
const NODE_HEIGHT = 116;
const ROW_GAP = 22;
const TOP = 48;

function statusLabel(task: Plan['tasks'][number], plan: Plan): string {
  if (task.status === 'queued') {
    if (plan.blocked.includes(task.local_id)) return 'Blocked';
    if (plan.ready.includes(task.local_id)) return 'Ready';
    return 'Waiting';
  }
  if (task.status === 'running') return 'Running';
  if (task.status === 'verifying') return 'Review';
  if (task.status === 'completed') return 'Complete';
  if (task.status === 'cancelled') return 'Cancelled';
  return task.status === 'failed' ? 'Failed' : 'Blocked';
}

export function PlanDependencyGraph({ plan, stages }: { plan: Plan; stages: Stages }) {
  if (stages.length === 0) return null;

  const positions = new Map<string, NodePosition>();
  stages.forEach(([stage, tasks]) => tasks.forEach((task, row) => {
    positions.set(task.local_id, {
      x: stage * (NODE_WIDTH + COLUMN_GAP),
      y: TOP + row * (NODE_HEIGHT + ROW_GAP),
      stage,
    });
  }));

  const columnCount = stages.length;
  const width = columnCount * NODE_WIDTH + Math.max(0, columnCount - 1) * COLUMN_GAP;
  const rowCount = Math.max(...stages.map(([, tasks]) => tasks.length));
  const height = TOP + rowCount * (NODE_HEIGHT + ROW_GAP) - ROW_GAP + 14;
  const canvasStyle = { width, height } as CSSProperties;

  return <section className="plan-dependency-map" aria-label="Task dependency map">
    <div className="plan-dependency-heading">
      <div><h3>Task map</h3><p>Arrows show prerequisite and subtask work that must finish before a parent can start.</p></div>
      <span>{plan.tasks.length} tasks · {stages.length} stages</span>
    </div>
    <div className="plan-dependency-viewport" tabIndex={0} aria-label="Scrollable task dependency map">
      <div className="plan-dependency-canvas" style={canvasStyle}>
        {stages.map(([stage]) => <h4
          className="plan-dependency-stage-title"
          key={stage}
          style={{ left: stage * (NODE_WIDTH + COLUMN_GAP), width: NODE_WIDTH }}
        >Stage {stage + 1}</h4>)}
        <svg className="plan-dependency-edges" width={width} height={height} aria-hidden="true">
          <defs><marker id="plan-dependency-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,6 L7,3 z" />
          </marker></defs>
          {plan.tasks.flatMap(task => [
            ...task.dependencies.map(dependency => ({fromId:dependency,toId:task.local_id})),
            ...plan.tasks.filter(child=>child.parent_id===task.local_id).map(child=>({fromId:child.local_id,toId:task.local_id})),
          ].map(({fromId,toId}) => {
            const from = positions.get(fromId);
            const to = positions.get(toId);
            if (!from || !to) return null;
            const x1 = from.x + NODE_WIDTH;
            const y1 = from.y + NODE_HEIGHT / 2;
            const x2 = to.x;
            const y2 = to.y + NODE_HEIGHT / 2;
            const curve = Math.max(24, (x2 - x1) / 2);
            return <path
              key={`${fromId}:${toId}`}
              d={`M ${x1} ${y1} C ${x1 + curve} ${y1}, ${x2 - curve} ${y2}, ${x2} ${y2}`}
              markerEnd="url(#plan-dependency-arrow)"
            />;
          }))}
        </svg>
        {plan.tasks.map(task => {
          const position = positions.get(task.local_id);
          if (!position) return null;
          const status = statusLabel(task, plan);
          return <a
            key={task.id}
            className={`plan-dependency-node is-${status.toLowerCase()}`}
            href={`#plan-task-${encodeURIComponent(task.local_id)}`}
            aria-label={`Open task ${task.local_id}, stage ${position.stage + 1}, ${status}`}
            title={task.objective}
            style={{ left: position.x, top: position.y, width: NODE_WIDTH }}
          >
            <strong>{task.local_id}</strong>
            <span className="plan-dependency-objective">{task.objective}</span>
            <span className="plan-dependency-meta">{task.assigned_role} · {task.expected_outputs.length} {task.expected_outputs.length===1?'expected output':'expected outputs'}</span>
            <small>{status}</small>
          </a>;
        })}
      </div>
    </div>
  </section>;
}
