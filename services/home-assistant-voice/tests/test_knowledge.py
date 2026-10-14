"""Read-only, freshness and source-scope regressions using isolated catalogs."""
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("knowledge", ROOT / "custom_components/edsys_voice_knowledge/knowledge.py")
knowledge = importlib.util.module_from_spec(spec); spec.loader.exec_module(knowledge)
spec = importlib.util.spec_from_file_location("export", ROOT / "export_knowledge.py")
exporter = importlib.util.module_from_spec(spec); spec.loader.exec_module(exporter)


class KnowledgeSafety(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "catalog.sqlite"
        with sqlite3.connect(self.path) as c:
            c.executescript(exporter.SCHEMA)
            c.executemany("insert into metadata values(?,?)", [("schema_version","2"),("voice_scope","edsys-only"),("built_at",datetime.now(timezone.utc).isoformat()),("manifest_sha256","fixture")])
            c.execute("insert into sources values(1,'fixture','40 Services/EdCore Proxmox/Node3.md','EdCore Proxmox Node3','EdSysVault_RAG',0,120,'fixture',1,'2026-10-13')")
            text="pve-node3 is the EdCore Proxmox host for Home Assistant and the isolated Kali lab."
            c.execute("insert into chunks values(1,1,0,?)",(text,))
            c.execute("insert into chunks_fts values(1,0,'EdCore Proxmox Node3','fixture',?)",(text,))

    def tearDown(self):
        self.directory.cleanup()

    def test_named_host_and_spoken_alias_find_current_evidence(self):
        result=knowledge.search(self.path,"What is node 3 used for in EdSys?")
        self.assertEqual(result["status"],"ok")
        self.assertIn("Home Assistant",result["sources"][0]["excerpt"])
        self.assertEqual(result["evidence_type"],"reviewed_documentation_not_live_telemetry")

    def test_stale_and_future_catalogs_do_not_return_evidence(self):
        for timestamp in (time.time()+knowledge.MAX_AGE_SECONDS+60,time.time()-120):
            result=knowledge.search(self.path,"pve-node3",now=timestamp)
            self.assertEqual(result["status"],"stale")
            self.assertEqual(result["sources"],[])

    def test_business_and_general_questions_do_not_expose_catalog(self):
        self.assertEqual(knowledge.search(self.path,"Foothills PSR in EdSys")["status"],"out_of_scope")
        self.assertEqual(knowledge.search(self.path,"Explain dew point")["status"],"not_an_edsys_query")

    def test_unknown_private_host_does_not_get_generic_edsys_evidence(self):
        self.assertEqual(knowledge.search(self.path,"EdSys nonexistenthost z9999")["status"],"no_matching_sources")

    def test_sql_input_cannot_write_or_break_catalog(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        result=knowledge.search(self.path,'pve-node3"; DROP TABLE sources; --')
        self.assertEqual(result["status"],"ok")
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)

    def test_missing_corrupt_and_symlink_files_fail_closed(self):
        missing=Path(self.directory.name)/"absent.sqlite"
        self.assertEqual(knowledge.search(missing,"pve-node3")["status"],"unavailable")
        alias=Path(self.directory.name)/"alias.sqlite";alias.symlink_to(self.path)
        self.assertEqual(knowledge.search(alias,"pve-node3")["status"],"unavailable")
        self.path.write_bytes(b"invalid")
        self.assertEqual(knowledge.search(self.path,"pve-node3")["status"],"unavailable")

    def test_export_excludes_business_sources_and_retains_dates(self):
        with sqlite3.connect(self.path) as c:
            c.execute("insert into sources values(2,'private','Foothills Billing.md','Foothills billing','EdSysVault_RAG',0,100,'private',0,'2026-10-13')")
            c.execute("insert into chunks values(2,2,0,'Business record must not enter voice catalog')")
        target=Path(self.directory.name)/"export.sqlite"
        report=exporter.export(target,self.path)
        self.assertEqual(report["sources"],1)
        with sqlite3.connect(target) as c:
            self.assertEqual(c.execute("select title from sources").fetchone()[0],"EdCore Proxmox Node3")
        self.assertEqual(knowledge.search(target,"node3")["status"],"ok")

    def test_oversized_queries_and_unknown_schema_are_rejected(self):
        self.assertEqual(knowledge.search(self.path,"x"*513)["status"],"invalid_query")
        with sqlite3.connect(self.path) as c:c.execute("update metadata set value='3' where key='schema_version'")
        self.assertEqual(knowledge.search(self.path,"pve-node3")["status"],"invalid_catalog")


if __name__=="__main__":unittest.main()
