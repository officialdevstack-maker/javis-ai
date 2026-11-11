import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { getSources, ingestFolder, removeSource } from "./api";
import type { IndexedSource, Scope } from "./api";

interface SourcesProps {
  onBack: () => void;
}

function isScope(value: string): value is Scope {
  return value === "private" || value === "public" || value === "business";
}

export default function Sources({ onBack }: SourcesProps) {
  const [folderPath, setFolderPath] = useState("data/notes");
  const [scope, setScope] = useState<Scope>("private");
  const [sources, setSources] = useState<IndexedSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function refreshSources(): Promise<void> {
    const response = await getSources();
    setSources(response.sources);
  }

  useEffect(() => {
    let active = true;
    async function loadSources(): Promise<void> {
      try {
        const response = await getSources();
        if (active) setSources(response.sources);
      } catch (loadError: unknown) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Could not load indexed sources.");
        }
      } finally {
        if (active) setLoading(false);
      }
    }

    void loadSources();
    return () => {
      active = false;
    };
  }, []);

  async function indexFolder(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const path = folderPath.trim();
    if (!path) {
      setError("Enter a folder path inside a backend ALLOWED_DIRS folder.");
      return;
    }
    if (!window.confirm(`Index documents from "${path}" into the local knowledge store?`)) return;

    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await ingestFolder(path, scope);
      await refreshSources();
      setNotice(`Indexed ${result.indexed_files} file(s) into ${result.indexed_chunks} chunk(s).`);
    } catch (ingestError: unknown) {
      setError(ingestError instanceof Error ? ingestError.message : "Could not index this folder.");
    } finally {
      setBusy(false);
    }
  }

  async function reindexFolder(source: IndexedSource): Promise<void> {
    if (!window.confirm(`Re-index all supported documents in "${source.folder_path}"?`)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await ingestFolder(source.folder_path, source.scope);
      await refreshSources();
      setNotice(`Re-indexed ${result.indexed_files} file(s) into ${result.indexed_chunks} chunk(s).`);
    } catch (reindexError: unknown) {
      setError(reindexError instanceof Error ? reindexError.message : "Could not re-index this folder.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteIndexedSource(source: IndexedSource): Promise<void> {
    if (!window.confirm(`Remove "${source.source}" from the local knowledge index?`)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await removeSource(source.id);
      await refreshSources();
      setNotice(`Removed ${source.source} from the index. The original file was not deleted.`);
    } catch (removeError: unknown) {
      setError(removeError instanceof Error ? removeError.message : "Could not remove this source.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Javis home" onClick={(event) => {
          event.preventDefault();
          onBack();
        }}>
          <span className="brand-mark" aria-hidden="true">J</span>
          <span>javis<span className="brand-ai">.ai</span></span>
        </a>
        <div className="header-actions">
          <span className="local-indicator"><span /> Local and private</span>
          <button className="new-chat-button" onClick={onBack}>Back to chat</button>
        </div>
      </header>

      <section className="sources-page">
        <div className="sources-heading">
          <p className="eyebrow">LOCAL KNOWLEDGE</p>
          <h1>Sources</h1>
          <p>Index documents stored in an allowed local folder. Files stay on this device.</p>
        </div>

        <form className="source-form" onSubmit={(event) => void indexFolder(event)}>
          <label htmlFor="source-folder">Folder path</label>
          <div className="source-input-row">
            <input
              id="source-folder"
              value={folderPath}
              onChange={(event) => setFolderPath(event.target.value)}
              placeholder="data/notes"
              disabled={busy}
            />
            <select
              aria-label="Knowledge scope"
              value={scope}
              onChange={(event) => {
                if (isScope(event.target.value)) setScope(event.target.value);
              }}
              disabled={busy}
            >
              <option value="private">Private</option>
              <option value="public">Public</option>
              <option value="business">Business</option>
            </select>
            <button type="submit" disabled={busy || !folderPath.trim()}>
              {busy ? "Working..." : "Index folder"}
            </button>
          </div>
          <p className="source-help">
            The folder must be inside one of the directories configured by <code>ALLOWED_DIRS</code> in
            <code> backend/.env</code>. Supported files: TXT, Markdown, PDF, and DOCX.
          </p>
        </form>

        {error && <p className="source-feedback error-message" role="alert">{error}</p>}
        {notice && <p className="source-feedback source-success" role="status">{notice}</p>}

        <section className="indexed-sources" aria-label="Indexed files">
          <div className="sources-list-heading">
            <h2>Indexed files</h2>
            <span>{sources.length}</span>
          </div>
          {loading ? (
            <p className="source-empty">Loading indexed sources...</p>
          ) : sources.length === 0 ? (
            <p className="source-empty">No documents indexed yet. Add a folder above to get started.</p>
          ) : (
            <div className="source-list">
              {sources.map((source) => (
                <article className="source-card" key={source.id}>
                  <div className="source-file-icon" aria-hidden="true">▤</div>
                  <div className="source-card-details">
                    <h3 title={source.path}>{source.source}</h3>
                    <p title={source.path}>{source.path}</p>
                    <div className="source-meta">
                      <span className={`scope-badge ${source.scope}`}>{source.scope}</span>
                      <span>{source.chunk_count} chunks</span>
                      <span>Indexed {new Date(source.indexed_at).toLocaleString()}</span>
                    </div>
                  </div>
                  <div className="source-actions">
                    <button onClick={() => void reindexFolder(source)} disabled={busy}>
                      Re-index folder
                    </button>
                    <button className="source-remove" onClick={() => void deleteIndexedSource(source)} disabled={busy}>
                      Remove
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </section>
    </main>
  );
}
