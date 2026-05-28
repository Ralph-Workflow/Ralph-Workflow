
import sys, io, contextlib, json, logging
sys.path.insert(0, '/home/mistlight/.openclaw/workspace')
logging.root.handlers = []

from unittest.mock import patch
from agents.marketing import seo_daily

# Realistic fake data
fake_homepage_html = (
    "<html lang='en'>"
    "<head><title>Ralph Workflow</title>"
    "<meta name='description' content='Preconfigured AI engineering workflow.'>"
    "<link rel='canonical' href='https://ralphworkflow.com'>"
    "<meta property='og:title' content='x'>"
    "<meta property='og:description' content='x'>"
    "<meta property='og:url' content='x'>"
    "<meta property='og:type' content='website'>"
    "<meta name='twitter:card' content='summary'>"
    "<script type='application/ld+json'>{"@type":"WebPage"}</script>"
    "</head>"
    "<body><h1>Ralph Workflow</h1><nav></nav><main></main></body>"
    "</html>"
)
fake_sitemap_xml = '<?xml version="1.0"?><urlset>' + ''.join(
    f"<loc>https://ralphworkflow.com/{i}</loc>" for i in range(1, 248)
) + '</urlset>'

responses = [
    (200, fake_homepage_html),
    (200, "Sitemap: https://ralphworkflow.com/sitemap.xml\n"),
    (200, fake_sitemap_xml),
    (200, ""), (200, ""), (200, ""), (200, ""), (200, ""),
]
resp_iter = iter(responses)

def fake_http_get(*a, **kw):
    return next(resp_iter)

buf = io.StringIO()
with patch.object(seo_daily, "http_get", fake_http_get), \
     patch.object(seo_daily, "track_ranks", return_value={}), \
     patch.object(seo_daily, "check_backlinks_google", return_value={"count_approx": 0}), \
     patch.object(seo_daily, "check_ahref_domain_rating", return_value={"dr": None}), \
     patch.object(seo_daily, "serp_features_for_keyword", return_value={}), \
     contextlib.redirect_stdout(buf), \
     contextlib.redirect_stderr(buf):
    try:
        seo_daily.main()
    except SystemExit:
        pass

sys.stdout.write(buf.getvalue())
