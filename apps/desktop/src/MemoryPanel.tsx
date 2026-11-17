import { useEffect, useState } from 'react';
import { EngineClient, type Memory } from './api/client.ts';

export function MemoryPanel({ client }: { client: EngineClient }) {
  const [items, setItems] = useState<Memory[]>([]);
  const [query, setQuery] = useState('');
  const [content, setContent] = useState('');
  const [editing, setEditing] = useState<string>();
  const [knowledge, setKnowledge] = useState('');
  const [source, setSource] = useState('');
  const [editingKnowledge, setEditingKnowledge] = useState<string>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setItems(await client.memories(query));
  }

  useEffect(() => {
    let active = true;
    void client
      .memories()
      .then(value => {
        if (active) setItems(value);
      })
      .catch(failure => {
        if (active) setError(String(failure));
      });
    return () => {
      active = false;
    };
  }, [client]);

  async function act(operation: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await operation();
      await refresh();
    } catch (failure) {
      setError(String(failure));
    } finally {
      setBusy(false);
    }
  }

  async function exportMemory() {
    const data = await client.exportMemory();
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'xaa-memory.json';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function clearPreferenceEditor() {
    setContent('');
    setEditing(undefined);
  }

  function clearKnowledgeEditor() {
    setKnowledge('');
    setSource('');
    setEditingKnowledge(undefined);
  }

  const preferences = items.filter(item => item.kind !== 'knowledge');
  const knowledgeItems = items.filter(item => item.kind === 'knowledge');

  return (
    <div className="memory-panel">
      <section className="memory-overview" aria-label="Memory overview">
        <div>
          <p className="eyebrow">YOUR PERSONAL CONTEXT</p>
          <h2>Memory, with you in control</h2>
          <p>
            Save preferences and sourced notes to guide future runs. X.A.A. treats saved text as
            unverified information; it never grants tool access or permissions.
          </p>
        </div>
        <div className="memory-counts" aria-label="Matching memory by type">
          <div><strong>{preferences.length}</strong><span>Preferences</span></div>
          <div><strong>{knowledgeItems.length}</strong><span>Sourced notes</span></div>
        </div>
      </section>

      <div className="memory-editors">
        <section className="memory-editor-card" aria-labelledby="memory-preference-heading">
          <div className="memory-card-heading">
            <span className="memory-card-icon" aria-hidden="true">✦</span>
            <div>
              <h3 id="memory-preference-heading">Preferences</h3>
              <p>How you like X.A.A. to work with you.</p>
            </div>
          </div>
          <label htmlFor="memory-preference">Preference</label>
          <textarea
            id="memory-preference"
            maxLength={4000}
            placeholder="For example: Keep explanations concise and show the files changed."
            value={content}
            onChange={event => setContent(event.target.value)}
          />
          <div className="memory-editor-footer">
            <small>{content.length}/4,000</small>
            <div className="controls">
              {editing && <button className="secondary-button" disabled={busy} onClick={clearPreferenceEditor}>Cancel edit</button>}
              <button
                disabled={busy || !content.trim()}
                onClick={() => act(async () => {
                  await client.savePreference(content, editing);
                  clearPreferenceEditor();
                })}
              >
                {editing ? 'Update preference' : 'Save preference'}
              </button>
            </div>
          </div>
        </section>

        <section className="memory-editor-card" aria-labelledby="memory-knowledge-heading">
          <div className="memory-card-heading">
            <span className="memory-card-icon memory-card-icon-blue" aria-hidden="true">↗</span>
            <div>
              <h3 id="memory-knowledge-heading">Sourced knowledge</h3>
              <p>Notes you want to keep with their reference.</p>
            </div>
          </div>
          <label htmlFor="memory-knowledge">Knowledge</label>
          <textarea
            id="memory-knowledge"
            maxLength={16384}
            placeholder="Add a fact or note that may be useful in a later run."
            value={knowledge}
            onChange={event => setKnowledge(event.target.value)}
          />
          <label htmlFor="memory-source">Source reference</label>
          <input
            id="memory-source"
            maxLength={2048}
            placeholder="URL, document title, or other reference"
            value={source}
            onChange={event => setSource(event.target.value)}
          />
          <div className="memory-editor-footer">
            <small>{knowledge.length}/16,384</small>
            <div className="controls">
              {editingKnowledge && <button className="secondary-button" disabled={busy} onClick={clearKnowledgeEditor}>Cancel edit</button>}
              <button
                disabled={busy || !knowledge.trim() || !source.trim()}
                onClick={() => act(async () => {
                  await client.saveSourcedKnowledge(knowledge, source, editingKnowledge);
                  clearKnowledgeEditor();
                })}
              >
                {editingKnowledge ? 'Update sourced knowledge' : 'Save sourced knowledge'}
              </button>
            </div>
          </div>
        </section>
      </div>

      <section className="memory-library" aria-labelledby="memory-library-heading">
        <div className="memory-library-heading">
          <div>
            <p className="eyebrow">SAVED FOR FUTURE RUNS</p>
            <h3 id="memory-library-heading">Your memory</h3>
          </div>
          <button className="secondary-button" disabled={busy} onClick={() => act(exportMemory)}>
            Export memory
          </button>
        </div>
        <div className="memory-search">
          <label htmlFor="memory-search">Search saved memory</label>
          <div className="memory-search-controls">
            <input
              id="memory-search"
              value={query}
              onChange={event => setQuery(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter') void act(refresh);
              }}
              placeholder="Search preferences and sourced notes"
            />
            <button className="secondary-button" disabled={busy} onClick={() => act(refresh)}>Search</button>
          </div>
          <small>{items.length} {items.length === 1 ? 'item' : 'items'} shown</small>
        </div>

        {items.length === 0 ? (
          <div className="memory-empty-state">
            <span aria-hidden="true">✦</span>
            <strong>{query.trim() ? 'No matching memories' : 'Your memory is ready when you are'}</strong>
            <p>{query.trim() ? 'Try a different search, or clear the search field.' : 'Add a preference or a sourced note above. You can edit, export, or delete it at any time.'}</p>
          </div>
        ) : (
          <div className="memory-record-groups">
            <MemoryGroup
              title="Preferences"
              items={preferences}
              emptyLabel="No preferences match this search."
              busy={busy}
              onEdit={item => { setContent(item.content); setEditing(item.id); }}
              onDelete={item => act(async () => {
                await client.deleteMemory(item.id);
                if (editing === item.id) clearPreferenceEditor();
                if (editingKnowledge === item.id) clearKnowledgeEditor();
              })}
            />
            <MemoryGroup
              title="Sourced knowledge"
              items={knowledgeItems}
              emptyLabel="No sourced notes match this search."
              busy={busy}
              onEdit={item => { setKnowledge(item.content); setSource(item.source_uri ?? ''); setEditingKnowledge(item.id); }}
              onDelete={item => act(async () => {
                await client.deleteMemory(item.id);
                if (editing === item.id) clearPreferenceEditor();
                if (editingKnowledge === item.id) clearKnowledgeEditor();
              })}
            />
          </div>
        )}
        {error && <p className="memory-error" role="alert">{error}</p>}
      </section>
    </div>
  );
}

function MemoryGroup({
  title,
  items,
  emptyLabel,
  busy,
  onEdit,
  onDelete,
}: {
  title: string;
  items: Memory[];
  emptyLabel: string;
  busy: boolean;
  onEdit: (item: Memory) => void;
  onDelete: (item: Memory) => void;
}) {
  return (
    <section className="memory-record-group" aria-label={title}>
      <div className="memory-group-heading">
        <h4>{title}</h4>
        <span>{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="memory-group-empty">{emptyLabel}</p>
      ) : (
        <ul className="memory-record-list">
          {items.map(item => (
            <li className="memory-record" key={item.id}>
              <div className="memory-record-copy">
                <p>{item.content}</p>
                <small>
                  {item.kind === 'knowledge'
                    ? `Source reference: ${item.source_uri ?? 'missing'}`
                    : `Saved from: ${item.provenance}`}
                </small>
              </div>
              <div className="memory-record-actions">
                <button className="secondary-button" disabled={busy} onClick={() => onEdit(item)}>
                  {item.kind === 'knowledge' ? 'Edit sourced knowledge' : 'Edit preference'}
                </button>
                <button className="memory-delete-button" disabled={busy} onClick={() => onDelete(item)}>
                  Delete memory
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
