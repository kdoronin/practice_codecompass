from __future__ import annotations

import math

from neo4j import GraphDatabase

from .config import settings


RELATIONS = ["IMPORTS", "INHERITS", "INSTANTIATES"]


class GraphStore:
    def __init__(self) -> None:
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    def close(self) -> None:
        self.driver.close()

    def ensure_schema(self) -> None:
        with self.driver.session() as session:
            session.run(
                "CREATE CONSTRAINT file_unique IF NOT EXISTS "
                "FOR (f:File) REQUIRE (f.group_slug, f.version, f.path) IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT group_unique IF NOT EXISTS "
                "FOR (g:CodeGroup) REQUIRE g.group_slug IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT group_version_unique IF NOT EXISTS "
                "FOR (v:GroupVersion) REQUIRE (v.group_slug, v.version) IS UNIQUE"
            )

    def write_graph(
        self,
        group_slug: str,
        display_name: str,
        version: int,
        files: list[str],
        edges: list[dict],
        commit_hash: str | None,
        progress_callback=None,
    ) -> None:
        self.ensure_schema()
        with self.driver.session() as session:
            session.run(
                """
                MERGE (g:CodeGroup {group_slug: $group_slug})
                SET g.display_name = $display_name
                MERGE (v:GroupVersion {group_slug: $group_slug, version: $version})
                SET v.created_at = datetime(),
                    v.commit_hash = $commit_hash
                MERGE (g)-[:HAS_VERSION]->(v)
                """,
                group_slug=group_slug,
                display_name=display_name,
                version=version,
                commit_hash=commit_hash,
            )

            file_batches = math.ceil(len(files) / 1000) if files else 0
            edge_batches = sum(math.ceil(len([e for e in edges if e["relation"] == relation]) / 1000) for relation in RELATIONS)
            total_batches = max(1, file_batches + edge_batches)
            done_batches = 0

            for i in range(0, len(files), 1000):
                batch = files[i : i + 1000]
                session.run(
                    """
                    UNWIND $files AS file_path
                    MERGE (f:File {group_slug: $group_slug, version: $version, path: file_path})
                    SET f.label = file_path
                    WITH f
                    MATCH (v:GroupVersion {group_slug: $group_slug, version: $version})
                    MERGE (v)-[:CONTAINS]->(f)
                    """,
                    files=batch,
                    group_slug=group_slug,
                    version=version,
                )
                done_batches += 1
                if progress_callback:
                    progress_callback(done_batches, total_batches, "writing-files")

            for relation in RELATIONS:
                rel_edges = [e for e in edges if e["relation"] == relation]
                for i in range(0, len(rel_edges), 1000):
                    batch = rel_edges[i : i + 1000]
                    session.run(
                        f"""
                        UNWIND $edges AS edge
                        MERGE (a:File {{group_slug: $group_slug, version: $version, path: edge.source}})
                        MERGE (b:File {{group_slug: $group_slug, version: $version, path: edge.target}})
                        MERGE (a)-[:{relation} {{group_slug: $group_slug, version: $version}}]->(b)
                        """,
                        edges=batch,
                        group_slug=group_slug,
                        version=version,
                    )
                    done_batches += 1
                    if progress_callback:
                        progress_callback(done_batches, total_batches, f"writing-{relation.lower()}")

    def update_group_display_name(self, group_slug: str, display_name: str) -> None:
        with self.driver.session() as session:
            session.run(
                "MATCH (g:CodeGroup {group_slug: $group_slug}) SET g.display_name = $display_name",
                group_slug=group_slug,
                display_name=display_name,
            )

    def rename_group_slug(self, old_slug: str, new_slug: str) -> None:
        with self.driver.session() as session:
            session.run(
                "MATCH (g:CodeGroup {group_slug: $old_slug}) SET g.group_slug = $new_slug",
                old_slug=old_slug,
                new_slug=new_slug,
            )
            session.run(
                "MATCH (v:GroupVersion {group_slug: $old_slug}) SET v.group_slug = $new_slug",
                old_slug=old_slug,
                new_slug=new_slug,
            )
            session.run(
                "MATCH (f:File {group_slug: $old_slug}) SET f.group_slug = $new_slug",
                old_slug=old_slug,
                new_slug=new_slug,
            )
            session.run(
                "MATCH ()-[r]->() WHERE r.group_slug = $old_slug SET r.group_slug = $new_slug",
                old_slug=old_slug,
                new_slug=new_slug,
            )

    def delete_group(self, group_slug: str) -> None:
        with self.driver.session() as session:
            session.run(
                """
                MATCH (f:File {group_slug: $group_slug})
                DETACH DELETE f
                """,
                group_slug=group_slug,
            )
            session.run(
                """
                MATCH (v:GroupVersion {group_slug: $group_slug})
                DETACH DELETE v
                """,
                group_slug=group_slug,
            )
            session.run(
                """
                MATCH (g:CodeGroup {group_slug: $group_slug})
                DETACH DELETE g
                """,
                group_slug=group_slug,
            )

    def graph_counts(self, group_slug: str, version: int) -> tuple[int, int]:
        with self.driver.session() as session:
            nodes = session.run(
                "MATCH (f:File {group_slug: $group_slug, version: $version}) RETURN count(f) AS c",
                group_slug=group_slug,
                version=version,
            ).single()["c"]
            edges = session.run(
                "MATCH (:File {group_slug: $group_slug, version: $version})-[r]->(:File {group_slug: $group_slug, version: $version}) RETURN count(r) AS c",
                group_slug=group_slug,
                version=version,
            ).single()["c"]
        return nodes, edges

    def get_graph(
        self,
        group_slug: str,
        version: int,
        mode: str,
        file_path: str | None,
        depth: int,
        node_limit: int,
        edge_limit: int,
    ) -> tuple[list[dict], list[dict], bool]:
        with self.driver.session() as session:
            if mode == "subgraph" and file_path:
                node_rows = session.run(
                    """
                    MATCH (root:File {group_slug: $group_slug, version: $version, path: $file_path})
                    CALL apoc.path.subgraphNodes(root, {
                      maxLevel: $depth,
                      relationshipFilter: 'IMPORTS|INHERITS|INSTANTIATES',
                      bfs: true
                    }) YIELD node
                    RETURN DISTINCT node.path AS path
                    LIMIT $node_limit
                    """,
                    group_slug=group_slug,
                    version=version,
                    file_path=file_path,
                    depth=depth,
                    node_limit=node_limit,
                ).data()
                paths = [r["path"] for r in node_rows]
            else:
                node_rows = session.run(
                    """
                    MATCH (f:File {group_slug: $group_slug, version: $version})
                    RETURN f.path AS path
                    ORDER BY f.path
                    LIMIT $node_limit
                    """,
                    group_slug=group_slug,
                    version=version,
                    node_limit=node_limit,
                ).data()
                paths = [r["path"] for r in node_rows]

            if not paths:
                return [], [], False

            edge_rows = session.run(
                """
                MATCH (a:File {group_slug: $group_slug, version: $version})-[r]->(b:File {group_slug: $group_slug, version: $version})
                WHERE a.path IN $paths AND b.path IN $paths
                RETURN a.path AS source, b.path AS target, type(r) AS relation
                LIMIT $edge_limit
                """,
                group_slug=group_slug,
                version=version,
                paths=paths,
                edge_limit=edge_limit,
            ).data()

            total_nodes, total_edges = self.graph_counts(group_slug, version)

        nodes = [{"id": p, "label": p.split("/")[-1], "path": p} for p in paths]
        edges = [
            {
                "id": f"{row['source']}::{row['relation']}::{row['target']}",
                "source": row["source"],
                "target": row["target"],
                "relation": row["relation"],
            }
            for row in edge_rows
        ]
        truncated = total_nodes > node_limit or total_edges > edge_limit
        return nodes, edges, truncated

    def get_file_node(self, group_slug: str, version: int, file_path: str) -> dict | None:
        with self.driver.session() as session:
            row = session.run(
                """
                MATCH (f:File {group_slug: $group_slug, version: $version, path: $path})
                RETURN f.path AS path
                """,
                group_slug=group_slug,
                version=version,
                path=file_path,
            ).single()
            if not row:
                return None
            return {"path": row["path"]}

    def neighbors(
        self,
        group_slug: str,
        version: int,
        file_path: str,
        direction: str,
        limit: int,
    ) -> list[dict]:
        with self.driver.session() as session:
            if direction == "in":
                query = """
                MATCH (n:File {group_slug: $group_slug, version: $version})-[r]->(f:File {group_slug: $group_slug, version: $version, path: $path})
                RETURN n.path AS neighbor, type(r) AS relation, 'incoming' AS direction
                ORDER BY relation, neighbor
                LIMIT $limit
                """
            elif direction == "out":
                query = """
                MATCH (f:File {group_slug: $group_slug, version: $version, path: $path})-[r]->(n:File {group_slug: $group_slug, version: $version})
                RETURN n.path AS neighbor, type(r) AS relation, 'outgoing' AS direction
                ORDER BY relation, neighbor
                LIMIT $limit
                """
            else:
                query = """
                MATCH (f:File {group_slug: $group_slug, version: $version, path: $path})-[r]-(n:File {group_slug: $group_slug, version: $version})
                RETURN n.path AS neighbor, type(r) AS relation,
                CASE WHEN startNode(r) = f THEN 'outgoing' ELSE 'incoming' END AS direction
                ORDER BY relation, direction, neighbor
                LIMIT $limit
                """
            rows = session.run(
                query,
                group_slug=group_slug,
                version=version,
                path=file_path,
                limit=limit,
            ).data()
        return rows

    def subgraph(self, group_slug: str, version: int, file_path: str, depth: int, limit: int) -> dict:
        nodes, edges, truncated = self.get_graph(
            group_slug=group_slug,
            version=version,
            mode="subgraph",
            file_path=file_path,
            depth=depth,
            node_limit=limit,
            edge_limit=limit * 4,
        )
        return {"nodes": nodes, "edges": edges, "truncated": truncated}


graph_store = GraphStore()
