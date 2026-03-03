import { useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { api } from './api';

const RELATION_COLORS = {
  IMPORTS: '#67e8f9',
  INHERITS: '#fbbf24',
  INSTANTIATES: '#fb7185',
};

function SectionTabs({ active, onChange }) {
  const tabs = [
    { id: 'projects', label: 'Projects' },
    { id: 'graph', label: 'Graph Viewer' },
    { id: 'mcp', label: 'MCP Control' },
  ];

  return (
    <div className="tabs">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          className={`tab ${active === tab.id ? 'active' : ''}`}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState('projects');
  const [projects, setProjects] = useState([]);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [versions, setVersions] = useState([]);
  const [status, setStatus] = useState('Ready');

  const [localForm, setLocalForm] = useState({
    display_name: '',
    local_path: '',
    auto_reindex_enabled: true,
    poll_interval_seconds: 60,
  });
  const [gitForm, setGitForm] = useState({
    display_name: '',
    repo_url: '',
    auto_reindex_enabled: true,
    poll_interval_seconds: 60,
  });
  const [folderBrowser, setFolderBrowser] = useState({
    root: '',
    current: '',
    parent: null,
    directories: [],
  });
  const [folderLoading, setFolderLoading] = useState(false);
  const [folderFilter, setFolderFilter] = useState('');

  const [editMap, setEditMap] = useState({});

  const [graphForm, setGraphForm] = useState({
    version: 'latest',
    mode: 'full',
    file_path: '',
    depth: 1,
    node_limit: 1200,
    edge_limit: 2500,
  });
  const [graphData, setGraphData] = useState({ nodes: [], edges: [], truncated: false, limit_message: null });
  const graphRef = useRef(null);
  const [forceSettings, setForceSettings] = useState({
    charge: -240,
    linkDistance: 140,
    cooldownTicks: 220,
  });

  const [mcpStatus, setMcpStatus] = useState({ running: false });
  const [mcpLogs, setMcpLogs] = useState([]);
  const [mcpConfigs, setMcpConfigs] = useState([]);
  const [mcpForm, setMcpForm] = useState({
    default_group_slug: '',
    default_version: 'latest',
    transport: 'streamable-http',
    host: '0.0.0.0',
    port: 8811,
    path: '/mcp',
    public_url: 'http://localhost:8811/mcp',
    stateless_http: true,
  });

  async function loadProjects() {
    const data = await api('/projects');
    setProjects(data);
    if (!selectedProjectId && data.length > 0) {
      setSelectedProjectId(data[0].id);
      setMcpForm((prev) => ({ ...prev, default_group_slug: data[0].group_slug }));
    }
  }

  async function loadVersions(projectId) {
    if (!projectId) {
      setVersions([]);
      return;
    }
    const data = await api(`/projects/${projectId}/versions`);
    setVersions(data);
  }

  async function loadMcpStatus() {
    const data = await api('/mcp/status');
    setMcpStatus(data);
  }

  async function loadMcpLogs() {
    const data = await api('/mcp/logs');
    setMcpLogs(data.lines || []);
  }

  async function loadMcpConfigs(formState) {
    const search = new URLSearchParams({
      version: String(formState.default_version || 'latest'),
      transport: String(formState.transport || 'streamable-http'),
      host: String(formState.host || '0.0.0.0'),
      port: String(formState.port || 8811),
      path: String(formState.path || '/mcp'),
    });
    if (formState.public_url) {
      search.set('public_url', formState.public_url);
    }
    if (formState.default_group_slug) {
      search.set('group_slug', formState.default_group_slug);
    }
    const data = await api(`/mcp/configs?${search.toString()}`);
    setMcpConfigs(data.items || []);
  }

  async function browseFolders(pathValue = null) {
    try {
      setFolderLoading(true);
      const query = pathValue ? `?path=${encodeURIComponent(pathValue)}` : '';
      const data = await api(`/local-folders${query}`);
      setFolderBrowser(data);
      setLocalForm((prev) => ({
        ...prev,
        local_path: prev.local_path || data.current || '',
      }));
    } catch (error) {
      setStatus(`Error: ${error.message}`);
    } finally {
      setFolderLoading(false);
    }
  }

  useEffect(() => {
    loadProjects().catch((e) => setStatus(`Error: ${e.message}`));
    loadMcpStatus().catch(() => {});
    loadMcpLogs().catch(() => {});
    browseFolders().catch(() => {});
  }, []);

  useEffect(() => {
    loadVersions(selectedProjectId).catch((e) => setStatus(`Error: ${e.message}`));
  }, [selectedProjectId]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (activeTab === 'mcp') {
        loadMcpStatus().catch(() => {});
        loadMcpLogs().catch(() => {});
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [activeTab]);

  useEffect(() => {
    const timer = setInterval(() => {
      loadProjects().catch(() => {});
      if (selectedProjectId) {
        loadVersions(selectedProjectId).catch(() => {});
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [selectedProjectId]);

  useEffect(() => {
    loadMcpConfigs(mcpForm).catch(() => {});
  }, [mcpForm]);

  async function addLocalProject(event) {
    event.preventDefault();
    if (!localForm.local_path) {
      setStatus('Select a mounted folder first');
      return;
    }
    try {
      setStatus('Creating local project...');
      await api('/projects/local', { method: 'POST', body: JSON.stringify(localForm) });
      setLocalForm((prev) => ({ ...prev, display_name: '', local_path: folderBrowser.current || '' }));
      await loadProjects();
      setStatus('Local project queued for indexing');
    } catch (error) {
      setStatus(`Error: ${error.message}`);
    }
  }

  async function addGitProject(event) {
    event.preventDefault();
    try {
      setStatus('Creating git project...');
      await api('/projects/git', { method: 'POST', body: JSON.stringify(gitForm) });
      setGitForm({ ...gitForm, display_name: '', repo_url: '' });
      await loadProjects();
      setStatus('Git project queued for indexing');
    } catch (error) {
      setStatus(`Error: ${error.message}`);
    }
  }

  async function saveProject(project) {
    try {
      const values = editMap[project.id] || {};
      await api(`/projects/${project.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          display_name: values.display_name ?? project.display_name,
          group_slug: values.group_slug ?? project.group_slug,
          auto_reindex_enabled: values.auto_reindex_enabled ?? project.auto_reindex_enabled,
          poll_interval_seconds: Number(values.poll_interval_seconds ?? project.poll_interval_seconds),
        }),
      });
      await loadProjects();
      setStatus(`Updated ${project.display_name}`);
    } catch (error) {
      setStatus(`Error: ${error.message}`);
    }
  }

  async function deleteProject(project) {
    if (!window.confirm(`Delete project "${project.display_name}" and remove its graph from Neo4j?`)) {
      return;
    }
    try {
      setStatus(`Deleting ${project.display_name}...`);
      await api(`/projects/${project.id}`, { method: 'DELETE' });
      const nextProjects = projects.filter((item) => item.id !== project.id);
      setProjects(nextProjects);
      if (selectedProjectId === project.id) {
        const nextSelected = nextProjects[0]?.id || '';
        setSelectedProjectId(nextSelected);
        if (!nextSelected) {
          setVersions([]);
          setGraphData({ nodes: [], edges: [], truncated: false, limit_message: null });
        }
      }
      setStatus(`Deleted ${project.display_name}`);
    } catch (error) {
      setStatus(`Error: ${error.message}`);
    }
  }

  async function reindexProject(projectId) {
    try {
      await api(`/projects/${projectId}/reindex`, { method: 'POST', body: JSON.stringify({ reason: 'manual' }) });
      setStatus('Reindex queued');
      await loadVersions(projectId);
    } catch (error) {
      setStatus(`Error: ${error.message}`);
    }
  }

  async function fetchGraph(event) {
    event.preventDefault();
    if (!selectedProjectId) {
      setStatus('Select project first');
      return;
    }

    try {
      const params = new URLSearchParams({
        version: String(graphForm.version),
        mode: graphForm.mode,
        depth: String(graphForm.depth),
        node_limit: String(graphForm.node_limit),
        edge_limit: String(graphForm.edge_limit),
      });
      if (graphForm.mode === 'subgraph') {
        params.set('file_path', graphForm.file_path);
      }
      const data = await api(`/projects/${selectedProjectId}/graph?${params.toString()}`);
      setGraphData(data);
      setStatus(`Loaded graph: ${data.nodes.length} nodes / ${data.edges.length} edges`);
    } catch (error) {
      setStatus(`Error: ${error.message}`);
    }
  }

  function reheatGraph() {
    const graph = graphRef.current;
    if (!graph) {
      return;
    }
    graph.d3ReheatSimulation();
    if (forceData.nodes.length > 0) {
      graph.zoomToFit(700, 70);
    }
  }

  async function startMcp() {
    try {
      await api('/mcp/start', { method: 'POST', body: JSON.stringify(mcpForm) });
      await loadMcpStatus();
      await loadMcpLogs();
      setStatus('MCP server started');
    } catch (error) {
      setStatus(`Error: ${error.message}`);
    }
  }

  async function stopMcp() {
    try {
      await api('/mcp/stop', { method: 'POST' });
      await loadMcpStatus();
      await loadMcpLogs();
      setStatus('MCP server stopped');
    } catch (error) {
      setStatus(`Error: ${error.message}`);
    }
  }

  const forceData = useMemo(() => {
    const degree = {};
    graphData.edges.forEach((edge) => {
      degree[edge.source] = (degree[edge.source] || 0) + 1;
      degree[edge.target] = (degree[edge.target] || 0) + 1;
    });

    const nodes = graphData.nodes.map((node) => ({
      id: node.id,
      label: node.label,
      path: node.path,
      degree: degree[node.id] || 0,
      color: '#38bdf8',
      size: 4 + Math.min(14, Math.sqrt((degree[node.id] || 0) + 1) * 2.2),
    }));

    const links = graphData.edges.map((edge) => ({
      source: edge.source,
      target: edge.target,
      relation: edge.relation,
      color: RELATION_COLORS[edge.relation] || '#7dd3fc',
    }));

    return { nodes, links };
  }, [graphData]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || forceData.nodes.length === 0) {
      return;
    }

    graph.d3Force('charge')?.strength(forceSettings.charge);
    graph.d3Force('link')?.distance(forceSettings.linkDistance);
    graph.zoomToFit(600, 70);
  }, [forceData, forceSettings]);

  const visibleDirectories = useMemo(() => {
    if (!folderFilter.trim()) {
      return folderBrowser.directories;
    }
    const q = folderFilter.toLowerCase();
    return folderBrowser.directories.filter((dir) => dir.name.toLowerCase().includes(q));
  }, [folderBrowser.directories, folderFilter]);

  function versionStatusText(project) {
    if (!project.latest_version) {
      return 'not indexed';
    }
    const status = project.latest_version_status || 'unknown';
    const stage = project.latest_version_stage ? ` / ${project.latest_version_stage}` : '';
    const percent =
      project.latest_version_progress_percent === null || project.latest_version_progress_percent === undefined
        ? ''
        : ` / ${project.latest_version_progress_percent}%`;
    return `v${project.latest_version} / ${status}${stage}${percent}`;
  }

  return (
    <div className="app-shell">
      <header className="hero">
        <h1>CodeCompass Platform</h1>
        <p>Versioned Python code graph indexing, visual exploration, and MCP delivery.</p>
      </header>

      <SectionTabs active={activeTab} onChange={setActiveTab} />

      <div className="status-pill">{status}</div>

      {activeTab === 'projects' && (
        <div className="grid two">
          <section className="panel">
            <h2>Add Local Project</h2>
            <form onSubmit={addLocalProject} className="form-grid">
              <input
                placeholder="Display name"
                value={localForm.display_name}
                onChange={(e) => setLocalForm({ ...localForm, display_name: e.target.value })}
              />
              <label>
                Selected folder
                <input value={localForm.local_path} readOnly placeholder="Choose folder below" required />
              </label>
              <div className="folder-browser-tools">
                <button
                  type="button"
                  onClick={() => browseFolders(folderBrowser.root)}
                  disabled={folderLoading || !folderBrowser.root}
                >
                  Root
                </button>
                <button
                  type="button"
                  onClick={() => folderBrowser.parent && browseFolders(folderBrowser.parent)}
                  disabled={folderLoading || !folderBrowser.parent}
                >
                  Up
                </button>
                <button type="button" onClick={() => browseFolders(folderBrowser.current)} disabled={folderLoading}>
                  Refresh
                </button>
              </div>
              <label>
                Folder filter
                <input
                  placeholder="Filter by name"
                  value={folderFilter}
                  onChange={(e) => setFolderFilter(e.target.value)}
                />
              </label>
              <div className="folder-browser-meta">
                <div>Root: {folderBrowser.root || '-'}</div>
                <div>Current: {folderBrowser.current || '-'}</div>
              </div>
              <div className="folder-browser-list">
                {folderLoading && <div className="folder-empty">Loading folders...</div>}
                {!folderLoading && visibleDirectories.length === 0 && (
                  <div className="folder-empty">No subfolders found</div>
                )}
                {!folderLoading &&
                  visibleDirectories.map((dir) => (
                    <div key={dir.path} className="folder-row">
                      <button type="button" className="folder-open" onClick={() => browseFolders(dir.path)}>
                        {dir.name}
                      </button>
                      <button
                        type="button"
                        className="folder-select"
                        onClick={() => setLocalForm((prev) => ({ ...prev, local_path: dir.path }))}
                      >
                        Select
                      </button>
                    </div>
                  ))}
              </div>
              <label>
                Poll interval (sec)
                <input
                  type="number"
                  min="15"
                  value={localForm.poll_interval_seconds}
                  onChange={(e) =>
                    setLocalForm({ ...localForm, poll_interval_seconds: Number(e.target.value || 60) })
                  }
                />
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={localForm.auto_reindex_enabled}
                  onChange={(e) => setLocalForm({ ...localForm, auto_reindex_enabled: e.target.checked })}
                />
                Auto reindex
              </label>
              <button type="submit">Create Local Project</button>
            </form>
          </section>

          <section className="panel">
            <h2>Add Public Git Project</h2>
            <form onSubmit={addGitProject} className="form-grid">
              <input
                placeholder="Display name"
                value={gitForm.display_name}
                onChange={(e) => setGitForm({ ...gitForm, display_name: e.target.value })}
              />
              <input
                placeholder="https://github.com/org/repo"
                value={gitForm.repo_url}
                onChange={(e) => setGitForm({ ...gitForm, repo_url: e.target.value })}
                required
              />
              <label>
                Poll interval (sec)
                <input
                  type="number"
                  min="15"
                  value={gitForm.poll_interval_seconds}
                  onChange={(e) =>
                    setGitForm({ ...gitForm, poll_interval_seconds: Number(e.target.value || 60) })
                  }
                />
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={gitForm.auto_reindex_enabled}
                  onChange={(e) => setGitForm({ ...gitForm, auto_reindex_enabled: e.target.checked })}
                />
                Auto reindex
              </label>
              <button type="submit">Create Git Project</button>
            </form>
          </section>

          <section className="panel span-all">
            <h2>Registered Projects</h2>
            <div className="project-list">
              {projects.map((project) => {
                const localEdit = editMap[project.id] || {};
                return (
                  <article
                    key={project.id}
                    className={`project-card ${selectedProjectId === project.id ? 'selected' : ''}`}
                    onClick={() => {
                      setSelectedProjectId(project.id);
                      setMcpForm((prev) => ({ ...prev, default_group_slug: project.group_slug }));
                    }}
                  >
                    <div className="project-top-row">
                      <strong>{project.display_name}</strong>
                      <span className="chip">{project.source_type}</span>
                      <span className="chip">latest: {project.latest_version ?? 'none'}</span>
                    </div>
                    <div className="project-progress-row">
                      <span>{versionStatusText(project)}</span>
                    </div>
                    <div className="project-progress-bar">
                      <span
                        style={{
                          width: `${Math.max(
                            0,
                            Math.min(100, Number(project.latest_version_progress_percent ?? 0))
                          )}%`,
                        }}
                      />
                    </div>
                    <div className="mini-grid">
                      <label>
                        Display name
                        <input
                          value={localEdit.display_name ?? project.display_name}
                          onChange={(e) =>
                            setEditMap({
                              ...editMap,
                              [project.id]: { ...localEdit, display_name: e.target.value },
                            })
                          }
                        />
                      </label>
                      <label>
                        Group slug
                        <input
                          value={localEdit.group_slug ?? project.group_slug}
                          onChange={(e) =>
                            setEditMap({
                              ...editMap,
                              [project.id]: { ...localEdit, group_slug: e.target.value },
                            })
                          }
                        />
                      </label>
                      <label>
                        Poll interval
                        <input
                          type="number"
                          min="15"
                          value={localEdit.poll_interval_seconds ?? project.poll_interval_seconds}
                          onChange={(e) =>
                            setEditMap({
                              ...editMap,
                              [project.id]: {
                                ...localEdit,
                                poll_interval_seconds: Number(e.target.value || 60),
                              },
                            })
                          }
                        />
                      </label>
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={localEdit.auto_reindex_enabled ?? project.auto_reindex_enabled}
                          onChange={(e) =>
                            setEditMap({
                              ...editMap,
                              [project.id]: { ...localEdit, auto_reindex_enabled: e.target.checked },
                            })
                          }
                        />
                        Auto reindex
                      </label>
                    </div>
                    <div className="button-row">
                      <button onClick={() => saveProject(project)}>Save</button>
                      <button onClick={() => reindexProject(project.id)}>Reindex</button>
                      <button className="danger" onClick={() => deleteProject(project)}>
                        Delete
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
        </div>
      )}

      {activeTab === 'graph' && (
        <div className="grid two">
          <section className="panel">
            <h2>Graph Controls</h2>
            <form onSubmit={fetchGraph} className="form-grid">
              <label>
                Project
                <select
                  value={selectedProjectId}
                  onChange={(e) => {
                    setSelectedProjectId(e.target.value);
                    const project = projects.find((item) => item.id === e.target.value);
                    if (project) {
                      setMcpForm((prev) => ({ ...prev, default_group_slug: project.group_slug }));
                    }
                  }}
                >
                  <option value="">Select</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.display_name} ({project.group_slug})
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Version
                <select
                  value={graphForm.version}
                  onChange={(e) => setGraphForm({ ...graphForm, version: e.target.value })}
                >
                  <option value="latest">latest</option>
                  {versions.map((v) => (
                    <option key={v.version} value={v.version}>
                      {v.version} ({v.status}, {v.stage}, {v.progress_percent}%)
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Mode
                <select
                  value={graphForm.mode}
                  onChange={(e) => setGraphForm({ ...graphForm, mode: e.target.value })}
                >
                  <option value="full">full</option>
                  <option value="subgraph">subgraph</option>
                </select>
              </label>

              {graphForm.mode === 'subgraph' && (
                <label>
                  File path
                  <input
                    placeholder="app/services/auth.py"
                    value={graphForm.file_path}
                    onChange={(e) => setGraphForm({ ...graphForm, file_path: e.target.value })}
                    required
                  />
                </label>
              )}

              <label>
                Depth
                <input
                  type="number"
                  min="1"
                  max="5"
                  value={graphForm.depth}
                  onChange={(e) => setGraphForm({ ...graphForm, depth: Number(e.target.value || 1) })}
                />
              </label>

              <label>
                Node limit
                <input
                  type="number"
                  min="100"
                  value={graphForm.node_limit}
                  onChange={(e) => setGraphForm({ ...graphForm, node_limit: Number(e.target.value || 100) })}
                />
              </label>

              <label>
                Edge limit
                <input
                  type="number"
                  min="100"
                  value={graphForm.edge_limit}
                  onChange={(e) => setGraphForm({ ...graphForm, edge_limit: Number(e.target.value || 100) })}
                />
              </label>

              <label>
                Repulsion ({forceSettings.charge})
                <input
                  type="range"
                  min="-800"
                  max="-40"
                  step="20"
                  value={forceSettings.charge}
                  onChange={(e) =>
                    setForceSettings((prev) => ({ ...prev, charge: Number(e.target.value) }))
                  }
                />
              </label>

              <label>
                Link distance ({forceSettings.linkDistance})
                <input
                  type="range"
                  min="30"
                  max="260"
                  step="10"
                  value={forceSettings.linkDistance}
                  onChange={(e) =>
                    setForceSettings((prev) => ({ ...prev, linkDistance: Number(e.target.value) }))
                  }
                />
              </label>

              <label>
                Physics ticks ({forceSettings.cooldownTicks})
                <input
                  type="range"
                  min="40"
                  max="500"
                  step="20"
                  value={forceSettings.cooldownTicks}
                  onChange={(e) =>
                    setForceSettings((prev) => ({ ...prev, cooldownTicks: Number(e.target.value) }))
                  }
                />
              </label>

              <div className="button-row">
                <button type="button" onClick={reheatGraph}>
                  Reheat Layout
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setForceSettings({
                      charge: -240,
                      linkDistance: 140,
                      cooldownTicks: 220,
                    })
                  }
                >
                  Default Physics
                </button>
              </div>

              <button type="submit">Load Graph</button>
            </form>

            {graphData.limit_message && <p className="warning">{graphData.limit_message}</p>}
          </section>

          <section className="panel graph-panel">
            <h2>Graph</h2>
            <div className="graph-count">nodes: {graphData.nodes.length} | edges: {graphData.edges.length}</div>
            <div className="graph-legend">
              <span><i style={{ background: RELATION_COLORS.IMPORTS }} /> IMPORTS</span>
              <span><i style={{ background: RELATION_COLORS.INHERITS }} /> INHERITS</span>
              <span><i style={{ background: RELATION_COLORS.INSTANTIATES }} /> INSTANTIATES</span>
            </div>
            <div className="force-wrapper">
              <ForceGraph2D
                ref={graphRef}
                graphData={forceData}
                backgroundColor="rgba(0,0,0,0)"
                cooldownTicks={forceSettings.cooldownTicks}
                linkDirectionalArrowLength={4}
                linkDirectionalArrowRelPos={0.95}
                linkColor={(link) => link.color}
                linkWidth={(link) => (link.relation === 'IMPORTS' ? 1.3 : 1.9)}
                nodeRelSize={6}
                nodeCanvasObject={(node, ctx, globalScale) => {
                  const label = node.label;
                  const fontSize = Math.max(8, 12 / globalScale);
                  ctx.font = `${fontSize}px Space Grotesk`;
                  const radius = node.size || 6;
                  const textWidth = ctx.measureText(label).width;
                  const boxWidth = textWidth + 10;
                  const boxHeight = fontSize + 6;

                  ctx.beginPath();
                  ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
                  ctx.fillStyle = node.color;
                  ctx.fill();

                  if (globalScale > 0.8) {
                    ctx.fillStyle = 'rgba(2, 6, 23, 0.72)';
                    ctx.fillRect(node.x + radius + 4, node.y - boxHeight / 2, boxWidth, boxHeight);
                    ctx.textAlign = 'left';
                    ctx.textBaseline = 'middle';
                    ctx.fillStyle = '#e0f2fe';
                    ctx.font = `${fontSize}px Space Grotesk`;
                    ctx.fillText(label, node.x + radius + 9, node.y);
                  }
                }}
                onNodeClick={(node) => {
                  setGraphForm((prev) => ({ ...prev, file_path: node.path, mode: 'subgraph' }));
                  setStatus(`Node selected: ${node.path}`);
                }}
                onEngineStop={() => {
                  if (graphRef.current && forceData.nodes.length > 0) {
                    graphRef.current.zoomToFit(500, 70);
                  }
                }}
              />
            </div>
          </section>
        </div>
      )}

      {activeTab === 'mcp' && (
        <div className="grid two">
          <section className="panel">
            <h2>MCP Runtime</h2>
            <div className="form-grid">
              <label>
                Default group_slug
                <input
                  value={mcpForm.default_group_slug}
                  onChange={(e) => setMcpForm({ ...mcpForm, default_group_slug: e.target.value })}
                  placeholder="my-project"
                />
              </label>
              <label>
                Default version
                <input
                  value={mcpForm.default_version}
                  onChange={(e) => setMcpForm({ ...mcpForm, default_version: e.target.value })}
                  placeholder="latest"
                />
              </label>
              <label>
                Transport
                <select
                  value={mcpForm.transport}
                  onChange={(e) => setMcpForm({ ...mcpForm, transport: e.target.value })}
                >
                  <option value="streamable-http">streamable-http</option>
                  <option value="http">http</option>
                  <option value="sse">sse</option>
                  <option value="stdio">stdio</option>
                </select>
              </label>
              <label>
                Host
                <input
                  value={mcpForm.host}
                  onChange={(e) => setMcpForm({ ...mcpForm, host: e.target.value })}
                  placeholder="0.0.0.0"
                />
              </label>
              <label>
                Port
                <input
                  type="number"
                  min="1"
                  max="65535"
                  value={mcpForm.port}
                  onChange={(e) => setMcpForm({ ...mcpForm, port: Number(e.target.value || 8811) })}
                />
              </label>
              <label>
                Path
                <input
                  value={mcpForm.path}
                  onChange={(e) => setMcpForm({ ...mcpForm, path: e.target.value })}
                  placeholder="/mcp"
                />
              </label>
              <label>
                Public URL
                <input
                  value={mcpForm.public_url}
                  onChange={(e) => setMcpForm({ ...mcpForm, public_url: e.target.value })}
                  placeholder="http://localhost:8811/mcp"
                />
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={mcpForm.stateless_http}
                  onChange={(e) => setMcpForm({ ...mcpForm, stateless_http: e.target.checked })}
                />
                Stateless HTTP
              </label>
              <div className="button-row">
                <button onClick={startMcp}>Start MCP</button>
                <button onClick={stopMcp}>Stop MCP</button>
              </div>
            </div>

            <div className="status-box">
              <div>running: {String(mcpStatus.running)}</div>
              <div>pid: {mcpStatus.pid ?? '-'}</div>
              <div>default group: {mcpStatus.default_group_slug ?? '-'}</div>
              <div>default version: {String(mcpStatus.default_version ?? '-')}</div>
              <div>transport: {mcpStatus.transport ?? '-'}</div>
              <div>url: {mcpStatus.url ?? '-'}</div>
              <div>command: {mcpStatus.command ?? '-'}</div>
            </div>
          </section>

          <section className="panel">
            <h2>MCP Logs</h2>
            <pre className="logs">{mcpLogs.join('\n') || 'No logs yet'}</pre>
          </section>

          <section className="panel span-all">
            <h2>Client Configs</h2>
            <div className="config-grid">
              {mcpConfigs.map((item) => (
                <article key={item.provider} className="config-card">
                  <h3>{item.provider}</h3>
                  <p>{item.description}</p>
                  <pre>{item.snippet}</pre>
                </article>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
