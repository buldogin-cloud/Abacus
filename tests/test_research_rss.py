import unittest
from unittest.mock import patch

from modules.researcher.researcher import Researcher
from modules.daily_briefing.collector import collect_researcher
from modules.daily_briefing.handoff import classify_handoff, build_handoff

URL = 'https://journal.example.org/rss'
SOURCE = dict(id='jpaic', label='JPAIC', url=URL, kind='clinical', keywords=[])

def feed(n=1, extra=''):
    items=''.join(f'<item><title>Clinical article {i}</title><link>https://journal.example.org/article/{i}</link><pubDate>Sun, 25 Oct 2026 10:00:00 GMT</pubDate></item>' for i in range(n))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Journal</title><link>https://journal.example.org</link><description>Fixture</description>{items}{extra}</channel></rss>'

class RSSTests(unittest.TestCase):
    def test_configured_feed_reaches_collector_without_normative_mislabel(self):
        with patch.object(Researcher, 'search_moz_updates', return_value=[]), patch.object(Researcher, 'search_nszu_updates', return_value=[]), patch.object(Researcher, 'search_kmu_updates', return_value=[]), patch.object(Researcher, '_fetch', return_value=(feed(), 'ok')):
            result=collect_researcher({'enabled':True, 'keywords':['наказ'], 'rss_sources':[SOURCE]}, '2026-10-24T00:00:00Z')
        self.assertEqual(result.status,'success')
        self.assertTrue(result.connection_verified)
        row=result.records[0]
        self.assertEqual(row['source'],'jpaic')
        self.assertEqual(row['document_type'],'стаття')
        self.assertFalse(row['source_verified'])
        self.assertEqual(row['research_kind'],'clinical')
        self.assertEqual(classify_handoff(row, {'source_id':'some-task'}, confident=True),'needs_review')
        self.assertEqual(result.meta['source_meta']['JPAIC']['coverage'],'feed_snapshot')

    def read(self, payload, status='ok', limit=5, keywords=None):
        r=Researcher(keywords=keywords)
        with patch.object(r,'_fetch',return_value=(payload,status)):
            rows=r.search_rss(URL,'JPAIC',limit)
        return r,rows

    def test_valid_empty_feed_is_empty(self):
        r,rows=self.read(feed(0))
        self.assertEqual(rows,[])
        self.assertEqual(r.source_status['JPAIC'],'empty')

    def test_html_is_error_not_empty(self):
        r,rows=self.read('<html><body>Login</body></html>')
        self.assertEqual(rows,[])
        self.assertEqual(r.source_status['JPAIC'],'error')

    def test_malformed_xml_is_error(self):
        r,rows=self.read(feed()[:-20])
        self.assertEqual(rows,[])
        self.assertEqual(r.source_status['JPAIC'],'error')

    def test_network_error_and_block_propagate(self):
        for status in ('blocked','error'):
            r,rows=self.read(None,status)
            self.assertEqual(rows,[])
            self.assertEqual(r.source_status['JPAIC'],status)

    def test_duplicate_urls_removed(self):
        extra='<item><title>Duplicate title</title><link>https://journal.example.org/article/0</link></item>'
        r,rows=self.read(feed(1,extra))
        self.assertEqual(len(rows),1)
        self.assertEqual(r.source_status['JPAIC'],'ok')

    def test_capped_snapshot_partial_not_complete(self):
        r,rows=self.read(feed(3),limit=2)
        self.assertEqual(len(rows),2)
        self.assertEqual(r.source_status['JPAIC'],'partial')
        self.assertTrue(r.source_meta['JPAIC']['truncated'])

    def test_invalid_link_is_partial(self):
        extra='<item><title>Unsafe item</title><link>javascript:alert(1)</link></item>'
        r,rows=self.read(feed(1,extra))
        self.assertEqual(len(rows),1)
        self.assertEqual(r.source_status['JPAIC'],'partial')

    def test_malformed_entry_url_does_not_abort_valid_items(self):
        extra='<item><title>Malformed link</title><link>https://[broken</link></item>'
        r,rows=self.read(feed(1,extra))
        self.assertEqual(len(rows),1)
        self.assertEqual(r.source_status['JPAIC'],'partial')
        self.assertEqual(r.source_meta['JPAIC']['invalid_entries'],1)

    def test_research_handoff_keeps_review_flag_for_long_title(self):
        for kind in ('clinical','research'):
            row=dict(source='jpaic',source_id='fixture',url='https://journal.example.org/article/1',
                     title='A sufficiently long research title that exceeds forty characters',
                     factual_summary='A sufficiently long factual summary that exceeds forty characters',
                     research_kind=kind,source_verified=False)
            classification=classify_handoff(row, {'source_id':'task-1'}, confident=True)
            handoff=build_handoff(row,classification,'medium',None,'fixture_run')
            self.assertEqual(handoff['classification'],'needs_review')
            self.assertTrue(handoff['evidence_quality']['needs_human_review'])
            self.assertIn('research_content_requires_review',handoff['evidence_quality']['quality_issues'])
            self.assertFalse(handoff['source_verified'])

    def test_keyword_filtered_empty_is_valid(self):
        r,rows=self.read(feed(),keywords=['absent'])
        self.assertEqual(rows,[])
        self.assertEqual(r.source_status['JPAIC'],'empty')

    def test_disabled_feed_makes_no_request(self):
        r=Researcher(rss_sources=[dict(SOURCE,enabled=False)])
        self.assertEqual(r.rss_sources,[])

    def test_configuration_rejects_bad_types_and_duplicate(self):
        for sources in ('bad',[None],[dict(SOURCE,url='http://journal.example.org')],[dict(SOURCE,url='https://secret@journal.example.org')],[SOURCE,SOURCE],[dict(SOURCE,keywords='bad')],[dict(SOURCE,id='moz_ukraine')],[dict(SOURCE,enabled='false')],[dict(SOURCE,enabled=0)]):
            with self.subTest(sources=sources),self.assertRaises(ValueError):
                Researcher(rss_sources=sources)

    def test_partial_propagates_from_feed_to_collector(self):
        with patch.object(Researcher,'search_moz_updates',return_value=[]), patch.object(Researcher,'search_nszu_updates',return_value=[]), patch.object(Researcher,'search_kmu_updates',return_value=[]), patch.object(Researcher,'_fetch',return_value=(feed(2),'ok')):
            result=collect_researcher({'enabled':True,'rss_sources':[SOURCE],'limit_per_source':1},'2026-10-24T00:00:00Z')
        self.assertEqual(result.status,'partial')

    def test_source_status_reset_between_collections(self):
        r=Researcher()
        r.source_status['old']='error'
        with patch.object(r,'search_moz_updates',return_value=[]),patch.object(r,'search_nszu_updates',return_value=[]),patch.object(r,'search_kmu_updates',return_value=[]):
            result=r.collect()
        self.assertNotIn('old',result['source_status'])

    def test_digest_includes_configured_journal(self):
        r=Researcher(rss_sources=[SOURCE])
        with patch.object(r,'search_moz_updates',return_value=[]),patch.object(r,'search_nszu_updates',return_value=[]),patch.object(r,'search_kmu_updates',return_value=[]),patch.object(r,'_fetch',return_value=(feed(),'ok')):
            digest=r.generate_digest()
        self.assertIn('## JPAIC',digest)
        self.assertIn('Clinical article 0',digest)

    def test_invalid_limit_rejected(self):
        for limit in (0,-1,True,'5'):
            with self.subTest(limit=limit),self.assertRaises(ValueError):
                Researcher().search_rss(URL,'JPAIC',limit)

if __name__=='__main__':
    unittest.main()
