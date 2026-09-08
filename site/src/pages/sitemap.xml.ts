import type { APIRoute } from 'astro';
import { getIndex } from '../lib/archive';

/**
 * Generated at build time rather than hand-maintained, so it cannot drift out
 * of step with the pages that actually exist.
 *
 * lastmod comes from the archive's own generated_at where a page displays
 * archive data, because for this site "when did this page change" and "when
 * did the pipeline last publish" are the same question.
 */
export const GET: APIRoute = ({ site }) => {
  const base = (site ?? new URL('https://pitfieldstresearch.com')).href.replace(/\/$/, '');
  const index = getIndex();
  const published = index?.generated_at ?? new Date().toISOString();
  const day = published.slice(0, 10);

  const pages: Array<[string, string]> = [
    ['/', '1.0'],
    ['/vol/SPY/', '0.9'],
    ['/quality/', '0.8'],
    ['/methodology/', '0.8'],
    ['/studies/', '0.8'],
    ['/studies/moon/', '0.6'],
    ['/reading/', '0.6'],
    ['/about/', '0.5'],
  ];

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${pages
  .map(
    ([path, priority]) => `  <url>
    <loc>${base}${path}</loc>
    <lastmod>${day}</lastmod>
    <priority>${priority}</priority>
  </url>`
  )
  .join('\n')}
</urlset>
`;

  return new Response(body, {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
