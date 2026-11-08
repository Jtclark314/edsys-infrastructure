#!/usr/bin/env python3
"""Reconcile saved native Visio package semantics independently of MCP summaries."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import posixpath
from urllib.parse import unquote
from xml.etree import ElementTree as E
from zipfile import ZipFile

NS = {"v": "http://schemas.microsoft.com/office/visio/2012/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "p": "http://schemas.openxmlformats.org/package/2006/relationships"}


def text(value):
    return " ".join((value or "").split())


def data_rows(shape, section="Property"):
    result = {}
    for row in shape.findall(f"v:Section[@N='{section}']/v:Row", NS):
        value = row.find("v:Cell[@N='Value']", NS)
        if value is not None:
            result[row.get("N")] = value.get("V", "")
    return result


def inspect(path: Path) -> dict:
    pages = []
    with ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"Corrupt ZIP entry: {bad}")
        rels = E.fromstring(archive.read("visio/pages/_rels/pages.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels}
        index = E.fromstring(archive.read("visio/pages/pages.xml"))
        for page in index.findall("v:Page", NS):
            rel = page.find("v:Rel", NS)
            member = posixpath.normpath("visio/pages/" + targets[rel.get("{" + NS["r"] + "}id")])
            xml = E.fromstring(archive.read(member))
            parents = {child: parent for parent in xml.iter() for child in parent}
            shapes = {}
            for s in xml.findall(".//v:Shape", NS):
                geometry = {c.get("N"): c.get("V") for c in s.findall("v:Cell", NS)
                            if c.get("N") in {"PinX", "PinY", "Width", "Height", "Angle"}}
                parent = parents.get(s)
                while parent is not None and parent.tag != "{" + NS["v"] + "}Shape":
                    parent = parents.get(parent)
                hyperlinks = [c.get("V", "") for c in s.findall("v:Section[@N='Hyperlink']/v:Row/v:Cell[@N='Address']", NS)]
                shapes[s.get("ID")] = {"nativeId": s.get("ID"), "name": s.get("NameU"),
                    "parentNativeId": parent.get("ID") if parent is not None else None,
                    "hyperlinks": hyperlinks,
                    "text": text("".join(s.find("v:Text", NS).itertext())) if s.find("v:Text", NS) is not None else "",
                    "data": data_rows(s), "user": data_rows(s, "User"), "geometry": geometry}
            connections = {}
            for c in xml.findall("v:Connects/v:Connect", NS):
                endpoint = c.get("FromCell")
                if endpoint in {"BeginX", "EndX"}:
                    connection = connections.setdefault(c.get("FromSheet"), {})
                    if endpoint in connection:
                        raise ValueError("Duplicate native endpoint attachment")
                    connection[endpoint] = c.get("ToSheet")
            nodes = [s for s in shapes.values() if s["data"].get("SourceID") and s["nativeId"] not in connections]
            source_by_native = {s["nativeId"]: s["data"].get("SourceID") for s in shapes.values()}
            edges = []
            for ident, endpoints in connections.items():
                s = shapes[ident]
                edges.append({"nativeId": ident, "id": s["data"].get("SourceID", s["user"].get("VisioModelMcp_Id")),
                    "source": source_by_native.get(endpoints.get("BeginX")),
                    "target": source_by_native.get(endpoints.get("EndX")),
                    "label": s["text"], "data": s["data"], "hyperlinks": s["hyperlinks"],
                    "gluedBothEnds": set(endpoints) == {"BeginX", "EndX"}})
            pages.append({"name": page.get("Name"), "nameU": page.get("NameU"),
                          "nodes": nodes, "edges": edges, "shapes": list(shapes.values()),
                          "nativeShapeCount": len(shapes)})
        content_types = archive.read("[Content_Types].xml").decode("utf-8")
    return {"file": str(path), "template": "application/vnd.ms-visio.template.main+xml" in content_types,
            "pages": pages}


def reconcile(actual: dict, model: dict) -> list[str]:
    errors = []
    if len(actual["pages"]) != len(model["pages"]):
        errors.append("Page count differs from source")
    for expected in model["pages"]:
        matches = [p for p in actual["pages"] if expected["name"] in (p["name"], p["nameU"])]
        if len(matches) != 1:
            errors.append(f"Missing/ambiguous page {expected['name']}")
            continue
        page = matches[0]
        nodes = {n["data"]["SourceID"]: n for n in page["nodes"]}
        if len(nodes) != len(page["nodes"]):
            errors.append(f"{expected['name']}: duplicate SourceID")
        expected_ids = {n["id"] for n in expected["nodes"]}
        extra = set(nodes) - expected_ids - set(expected.get("auxiliarySourceIDs", []))
        if extra:
            errors.append(f"{expected['name']}: unexpected SourceID {sorted(extra)}")
        allowed_text = {text(s) for s in expected.get("decorations", [])}
        by_native = {s["nativeId"]: s for s in page.get("shapes", [])}
        edge_native_ids = {e["nativeId"] for e in page["edges"]}
        for shape in page.get("shapes", []):
            if not shape["text"] or shape["data"].get("SourceID") or shape["nativeId"] in edge_native_ids:
                continue
            parent = by_native.get(shape.get("parentNativeId"))
            if parent and parent["data"].get("SourceID") in expected.get("auxiliarySourceIDs", []):
                continue
            if shape["text"] not in allowed_text:
                errors.append(f"{expected['name']}: unattributed text shape {shape['nativeId']} {shape['text']!r}")
        # Native lanes/containers can have their own IDs, but aren't process nodes.
        semantic_nodes = {ident: node for ident, node in nodes.items() if ident in expected_ids}
        if len(semantic_nodes) != len(expected_ids):
            errors.append(f"{expected['name']}: missing source nodes {sorted(expected_ids-set(nodes))}")
        for node in expected["nodes"]:
            found = nodes.get(node["id"])
            if found is None:
                continue
            if found["text"] != text(node["text"]):
                errors.append(f"{node['id']}: label differs")
            if node.get("status") and found["data"].get("Status") != node["status"]:
                errors.append(f"{node['id']}: Status differs")
            source = node.get("source", {})
            for source_key, field in (("reference", "SourceReference"), ("sha256", "SourceHash")):
                if source.get(source_key) and found["data"].get(field) != source[source_key]:
                    errors.append(f"{node['id']}: {field} differs")
            if source.get("url") and unquote(source["url"]) not in [unquote(u) for u in found.get("hyperlinks", [])]:
                errors.append(f"{node['id']}: source hyperlink missing/different")
            if model.get("asOf") and found["data"].get("AsOf") != model["asOf"]:
                errors.append(f"{node['id']}: AsOf differs")
        want = Counter((e["source"], e["target"], text(e.get("label", ""))) for e in expected["edges"])
        got = Counter((e["source"], e["target"], e["label"]) for e in page["edges"])
        if want != got:
            errors.append(f"{expected['name']}: relationship mismatch missing={list((want-got).elements())} extra={list((got-want).elements())}")
        if not all(e["gluedBothEnds"] for e in page["edges"]):
            errors.append(f"{expected['name']}: unglued connector")
        for edge in expected["edges"]:
            # source is the endpoint ID; provenance holds connector source evidence.
            # Older graph-only models do not require connector metadata or AsOf.
            if not any(key in edge for key in ("status", "provenance", "asOf")):
                continue
            ident = edge.get("id")
            if not isinstance(ident, str) or not ident.strip():
                errors.append(f"{expected['name']}: edge metadata requires an exact SourceID")
                continue
            matches = [e for e in page["edges"] if e["data"].get("SourceID") == ident]
            if len(matches) != 1:
                errors.append(f"{expected['name']}: missing/ambiguous edge SourceID {ident!r}")
                continue
            found = matches[0]
            if (found["source"], found["target"], found["label"]) != (edge["source"], edge["target"], text(edge.get("label", ""))):
                errors.append(f"{ident}: relationship differs")
            if "status" in edge and found["data"].get("Status") != edge["status"]:
                errors.append(f"{ident}: Status differs")
            provenance = edge.get("provenance", {})
            if not isinstance(provenance, dict):
                errors.append(f"{ident}: provenance must be an object")
                provenance = {}
            for source_key, field in (("reference", "SourceReference"), ("sha256", "SourceHash")):
                if source_key in provenance and found["data"].get(field) != provenance[source_key]:
                    errors.append(f"{ident}: {field} differs")
            if "url" in provenance and unquote(provenance["url"]) not in [unquote(u) for u in found["hyperlinks"]]:
                errors.append(f"{ident}: source hyperlink missing/different")
            if "asOf" in edge or "asOf" in model:
                if found["data"].get("AsOf") != edge.get("asOf", model.get("asOf")):
                    errors.append(f"{ident}: AsOf differs")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drawing", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--output", type=Path)
    opts = parser.parse_args()
    result = inspect(opts.drawing)
    if opts.model:
        result["errors"] = reconcile(result, json.loads(opts.model.read_text(encoding="utf-8-sig")))
        result["reconciled"] = not result["errors"]
    if opts.output:
        opts.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"reconciled": result.get("reconciled"), "errors": result.get("errors", []),
        "template": result["template"], "pages": [{"name": p["name"], "sourceNodes": len(p["nodes"]),
        "gluedConnections": len(p["edges"])} for p in result["pages"]]}, indent=2))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
