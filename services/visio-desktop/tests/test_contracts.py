import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from xml.etree import ElementTree as E
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_batch
import run_native_codex
import verify_vsdx

V = verify_vsdx.NS['v']
R = verify_vsdx.NS['r']
P = verify_vsdx.NS['p']


class NativeReceiptContracts(unittest.TestCase):
    def test_startup_failures_remain_visible_without_starting_codex(self):
        for session_error in (True, False):
            with self.subTest(session_error=session_error), tempfile.TemporaryDirectory() as home:
                root = Path(home) / 'Documents' / 'VisioMcp' / 'EdSysPilot'
                (root / 'jobs').mkdir(parents=True)
                (root / '.codex').mkdir()
                (root / '.codex' / 'config.toml').write_text('malformed [')
                prompt = root / 'jobs' / 'inspection.txt'
                prompt.write_text('Inspect only')
                with mock.patch.object(Path, 'home', return_value=Path(home)), \
                        mock.patch.object(sys, 'argv', ['run_native_codex.py', str(prompt), '--read-only']), \
                        mock.patch.object(run_native_codex, 'assert_interactive_windows',
                            side_effect=RuntimeError('non-console') if session_error else None), \
                        mock.patch.object(run_native_codex.subprocess, 'Popen') as start, \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(run_native_codex.main(), 1)
                start.assert_not_called()
                receipts = list((root / 'jobs').glob('*/receipt.json'))
                self.assertEqual(len(receipts), 1)
                receipt = json.loads(receipts[0].read_text())
                self.assertEqual(receipt['status'], 'failed')
                self.assertTrue(receipt['readOnly'])
                self.assertIn('finishedUtc', receipt)
                self.assertIn('error', receipt)


def fixture(path, *, reverse=False, unsourced=False, missing_provenance=False, duplicate=False,
            as_of=None, edge_data=None, edge_hyperlinks=()):
    page = E.Element('{' + V + '}PageContents')
    shapes = E.SubElement(page, '{' + V + '}Shapes')
    def shape(ident, label, source_id, status='Example', data_updates=None, hyperlinks=()):
        s = E.SubElement(shapes, '{' + V + '}Shape', ID=str(ident))
        E.SubElement(s, '{' + V + '}Text').text = label
        section = E.SubElement(s, '{' + V + '}Section', N='Property')
        data = {'Status': status}
        if as_of is not None:
            data['AsOf'] = as_of
        if source_id:
            data['SourceID'] = source_id
        if source_id == 'A' and not missing_provenance:
            data['SourceReference'] = 'Example source'
        data.update(data_updates or {})
        for key, value in data.items():
            if value is None:
                continue
            row = E.SubElement(section, '{' + V + '}Row', N=key)
            E.SubElement(row, '{' + V + '}Cell', N='Value', V=value)
        if hyperlinks:
            section = E.SubElement(s, '{' + V + '}Section', N='Hyperlink')
            for index, url in enumerate(hyperlinks):
                row = E.SubElement(section, '{' + V + '}Row', N=f'Link{index}')
                E.SubElement(row, '{' + V + '}Cell', N='Address', V=url)
    shape(1, 'Start', 'A')
    shape(2, 'Review', 'B')
    shape(3, 'Next', 'E1', data_updates=edge_data, hyperlinks=edge_hyperlinks)
    if unsourced:
        shape(4, 'Unreviewed step', None)
    if duplicate:
        shape(5, 'Start', 'A')
    connects = E.SubElement(page, '{' + V + '}Connects')
    for cell, target in [('BeginX', '2' if reverse else '1'), ('EndX', '1' if reverse else '2')]:
        E.SubElement(connects, '{' + V + '}Connect', FromSheet='3', FromCell=cell, ToSheet=target)
    index = E.Element('{' + V + '}Pages')
    p = E.SubElement(index, '{' + V + '}Page', Name='Example', NameU='Example')
    E.SubElement(p, '{' + V + '}Rel', {'{' + R + '}id': 'rId1'})
    rels = E.Element('{' + P + '}Relationships')
    E.SubElement(rels, '{' + P + '}Relationship', Id='rId1', Target='page1.xml')
    with ZipFile(path, 'w') as z:
        z.writestr('visio/pages/pages.xml', E.tostring(index))
        z.writestr('visio/pages/_rels/pages.xml.rels', E.tostring(rels))
        z.writestr('visio/pages/page1.xml', E.tostring(page))
        z.writestr('[Content_Types].xml', '<Types/>')


class SavedPackageContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / 'example.vsdx'
        self.model = {'pages': [{'name': 'Example', 'nodes': [
            {'id': 'A', 'text': 'Start', 'status': 'Example', 'source': {'reference': 'Example source'}},
            {'id': 'B', 'text': 'Review', 'status': 'Example'}],
            'edges': [{'source': 'A', 'target': 'B', 'label': 'Next'}]}]}

    def errors(self, **options):
        fixture(self.file, **options)
        return verify_vsdx.reconcile(verify_vsdx.inspect(self.file), self.model)

    def test_correct_saved_graph_and_provenance(self):
        self.assertEqual(self.errors(), [])

    def test_reversed_connection_is_rejected(self):
        self.assertTrue(any('relationship mismatch' in e for e in self.errors(reverse=True)))

    def test_unsourced_semantic_shape_is_reported(self):
        self.assertTrue(any('unattributed text shape' in e for e in self.errors(unsourced=True)))

    def test_missing_source_provenance_is_rejected(self):
        self.assertTrue(any('SourceReference differs' in e for e in self.errors(missing_provenance=True)))

    def test_duplicate_source_id_is_rejected(self):
        self.assertTrue(any('duplicate SourceID' in e for e in self.errors(duplicate=True)))

    def test_missing_step_and_wrong_status_are_rejected(self):
        self.model['pages'][0]['nodes'][1]['status'] = 'Approved'
        self.model['pages'][0]['nodes'].append({'id': 'C', 'text': 'Closeout'})
        errors = self.errors()
        self.assertTrue(any('Status differs' in e for e in errors))
        self.assertTrue(any('missing source nodes' in e for e in errors))

    def test_requested_edge_metadata_is_inventoried_and_reconciled(self):
        self.model['asOf'] = '2026-11-07'
        self.model['pages'][0]['edges'][0].update(id='E1', status='Example', provenance={
            'reference': 'Example connector source', 'sha256': 'example-source-hash',
            'url': 'https://example.org/source'})
        fixture(self.file, as_of=self.model['asOf'], edge_data={
            'SourceReference': 'Example connector source', 'SourceHash': 'example-source-hash'},
            edge_hyperlinks=['https://example.org/source'])
        actual = verify_vsdx.inspect(self.file)
        edge = actual['pages'][0]['edges'][0]
        self.assertEqual(edge['data']['SourceReference'], 'Example connector source')
        self.assertEqual(edge['hyperlinks'], ['https://example.org/source'])
        self.assertEqual(verify_vsdx.reconcile(actual, self.model), [])

    def test_missing_requested_edge_provenance_is_rejected(self):
        self.model['pages'][0]['edges'][0].update(id='E1', status='Example', provenance={
            'reference': 'Example connector source', 'sha256': 'example-source-hash',
            'url': 'https://example.org/source'})
        errors = self.errors()
        self.assertIn('E1: SourceReference differs', errors)
        self.assertIn('E1: SourceHash differs', errors)
        self.assertIn('E1: source hyperlink missing/different', errors)

    def test_requested_edge_identity_status_and_date_are_checked(self):
        self.model['pages'][0]['edges'][0].update(id='E1', status='Approved', asOf='2026-11-08')
        errors = self.errors()
        self.assertIn('E1: Status differs', errors)
        self.assertIn('E1: AsOf differs', errors)
        self.model['pages'][0]['edges'][0]['id'] = 'wrong-edge'
        self.assertTrue(any('missing/ambiguous edge SourceID' in e for e in self.errors()))
        del self.model['pages'][0]['edges'][0]['id']
        self.assertTrue(any('edge metadata requires an exact SourceID' in e for e in self.errors()))

    def test_requested_edge_identity_binds_metadata_to_the_exact_relationship(self):
        self.model['pages'][0]['edges'][0].update(id='E1', status='Example')
        self.model['pages'][0]['edges'].append({
            'id': 'E2', 'source': 'B', 'target': 'A', 'label': 'Return', 'status': 'Example'})
        fixture(self.file)
        actual = verify_vsdx.inspect(self.file)
        first = actual['pages'][0]['edges'][0]
        second = copy.deepcopy(first)
        second.update(nativeId='4', id='E1', source='B', target='A', label='Return')
        first['id'] = first['data']['SourceID'] = 'E2'
        actual['pages'][0]['edges'].append(second)
        errors = verify_vsdx.reconcile(actual, self.model)
        self.assertIn('E1: relationship differs', errors)
        self.assertIn('E2: relationship differs', errors)
        self.assertFalse(any('relationship mismatch' in e for e in errors))

    def test_edge_date_overrides_model_date_and_unrequested_metadata_is_optional(self):
        self.model['asOf'] = '2026-11-07'
        self.assertEqual(self.errors(as_of='2026-11-07', edge_data={'AsOf': None}), [])
        self.model['pages'][0]['edges'][0].update(id='E1', status='Example')
        self.assertIn('E1: AsOf differs', self.errors(as_of='2026-11-07', edge_data={'AsOf': None}))
        self.model['pages'][0]['edges'][0].update(id='E1', asOf='2026-11-08')
        self.assertEqual(self.errors(as_of='2026-11-07', edge_data={'AsOf': '2026-11-08'}), [])


class BatchContracts(unittest.TestCase):
    def test_reference_uses_unique_returned_identity(self):
        self.assertEqual(mcp_batch.resolve({'$ref': 'created.diagramId'}, {'created': {'output': {'diagramId': 'issued-identity'}}}), 'issued-identity')

    def test_missing_or_ambiguous_identity_stops(self):
        for value in ({}, {'items': [{'diagramId': 'one'}, {'diagramId': 'two'}]}):
            with self.assertRaises(ValueError):
                mcp_batch.resolve({'$ref': 'created.diagramId'}, {'created': value})

    def test_unapproved_tool_stops_before_transport(self):
        client = mcp_batch.Client.__new__(mcp_batch.Client)
        with self.assertRaises(ValueError):
            client.call('arbitrary_command', {})


if __name__ == '__main__':
    unittest.main()
