// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// The guide's pages come from docs/ (scripts/sync-docs.mjs); the sidebar lists them in the order a reader meets them.
export default defineConfig({
  site: 'https://swm.pipelab.dev',
  integrations: [
    starlight({
      title: 'semantic-warehouse-mcp',
      description:
        'A read-only MCP server that lets any AI agent work with SQL databases the way an analyst does: knowing what the data means — a semantic layer in Apache Ossie, your own metadata, context and skills.',
      favicon: '/favicon.svg',
      customCss: ['./src/styles.css'],
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/alexeiveselov92/semantic-warehouse-mcp' }],
      lastUpdated: false,
      sidebar: [
        { label: 'Getting started', link: '/getting-started/' },
        {
          label: 'Reference',
          items: [
            { label: 'Tools', link: '/reference/tools/' },
            { label: 'Configuration', link: '/reference/configuration/' },
            { label: 'Semantic models and vocabularies', link: '/reference/semantic-models/' },
            { label: 'The dbt importer', link: '/reference/dbt-importer/' },
            { label: 'The database importer', link: '/reference/database-importer/' },
            { label: 'The guard', link: '/reference/guard/' },
            { label: 'Engines', link: '/reference/engines/' },
            { label: 'Running it', link: '/reference/running/' },
          ],
        },
        { label: 'About this guide', link: '/guide/' },
        { label: 'Changelog', link: '/changelog/' },
      ],
    }),
  ],
});
