'use strict';

// Run: node --test tests/test_brain_view.js
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { resolveLink, tokenizeInline, markdownBlocks, filterNotes, filterTodos, layoutGraph } = require('../docs/brain-view.js');

test('note links navigate within the exported graph and preserve heading fragments', () => {
    const ids = new Set(['brain/index.md', 'brain/research/groupings.md', 'brain/research/predictions.md']);
    assert.deepEqual(resolveLink('predictions.md#forward-evaluation', 'brain/research/groupings.md', ids), { kind: 'note', id: 'brain/research/predictions.md', fragment: 'forward-evaluation' });
    assert.deepEqual(resolveLink('../index.md', 'brain/research/groupings.md', ids), { kind: 'note', id: 'brain/index.md', fragment: '' });
    assert.deepEqual(resolveLink('#research-todos', 'brain/research/groupings.md', ids), { kind: 'note', id: 'brain/research/groupings.md', fragment: 'research-todos' });
});

test('source links pin repository revision and permit safe external links', () => {
    const ids = new Set();
    assert.equal(resolveLink('../../models.py#L25', 'brain/research/groupings.md', ids, 'abcdef123456').url, 'https://github.com/netzo92/deanonymizing_xmr/blob/abcdef123456/models.py#L25');
    assert.equal(resolveLink('/README.md', 'brain/research/groupings.md', ids, 'unknown').url, 'https://github.com/netzo92/deanonymizing_xmr/blob/main/README.md');
    assert.equal(resolveLink('../../notes/a file.md', 'brain/research/groupings.md', ids).url, 'https://github.com/netzo92/deanonymizing_xmr/blob/main/notes/a%20file.md');
    assert.equal(resolveLink('https://www.getmonero.org/resources/', 'brain/index.md', ids).kind, 'external');
});

test('unsafe link schemes, hidden control characters, and outside-repository traversal are rejected', () => {
    for (const href of ['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'file:///tmp/secret', '//example.com', 'java\nscript:alert(1)', '../../../secret', '%2e%2e/%2e%2e/%2e%2e/secret', '..\\secret', '%0Ajavascript:alert(1)']) {
        assert.equal(resolveLink(href, 'brain/research/groupings.md', new Set()), null, href);
    }
});

test('inline parsing preserves raw HTML as text, code as code, and balanced link URLs', () => {
    const tokens = tokenizeInline('<img src=x onerror=alert(1)> **Hypothesis** `javascript:alert(1)` [source](https://example.com/a_(b))');
    assert.equal(tokens[0].type, 'text');
    assert.ok(tokens[0].text.includes('<img'));
    assert.ok(tokens.some(token => token.type === 'strong' && token.text === 'Hypothesis'));
    assert.ok(tokens.some(token => token.type === 'code' && token.text === 'javascript:alert(1)'));
    assert.equal(tokens.at(-1).href, 'https://example.com/a_(b)');
    assert.deepEqual(tokenizeInline('`[link](javascript:alert(1))`'), [{ type: 'code', text: '[link](javascript:alert(1))' }]);
});

test('Markdown blocks retain checklist state, tables, code fences, and ordinary lists', () => {
    const blocks = markdownBlocks('# Research\n\n- [ ] **G1** Evaluate overlap.\n- [x] Add evidence.\n\n| Metric | Meaning |\n| --- | --- |\n| Precision | Hypothesis quality |\n\n```html\n<img onerror=bad()>\n```\n\n1. Freeze data.\n2. Test later.');
    assert.equal(blocks[0].type, 'heading');
    assert.equal(blocks[1].checklist, true);
    assert.deepEqual(blocks[1].items.map(item => item.done), [false, true]);
    assert.deepEqual(blocks[2].headers, ['Metric', 'Meaning']);
    assert.equal(blocks[3].text, '<img onerror=bad()>');
    assert.equal(blocks[4].ordered, true);
});

test('TODO filters count checklist entries rather than notes and preserve open/completed separation', () => {
    const nodes = [
        { id: 'a', branch: 'research', title: 'Grouping', summary: 'Controlled labels', content: 'Uncertain overlap', todos: [{ text: 'Validate pairwise precision', done: false }, { text: 'Preserve candidate alternatives', done: true }] },
        { id: 'b', branch: 'tasks', title: 'Dashboard', todos: [{ text: 'Add grouping graph', done: false }] },
    ];
    assert.equal(filterTodos(nodes, { status: 'open' }).length, 2);
    assert.equal(filterTodos(nodes, { status: 'done' }).length, 1);
    assert.equal(filterTodos(nodes, { branch: 'research', query: 'precision', status: 'open' }).length, 1);
    assert.equal(filterTodos(nodes, { branch: 'tasks', status: 'done' }).length, 0);
    assert.equal(filterNotes(nodes, { query: 'uncertain' }).length, 1);
});

test('graph layout includes every note once, with finite coordinates and deterministic placement', () => {
    const nodes = [
        { id: 'brain/index.md', branch: 'root' },
        { id: 'brain/research/index.md', branch: 'research' },
        { id: 'brain/research/groupings.md', branch: 'research' },
        { id: 'brain/tasks/index.md', branch: 'tasks' },
    ];
    const layout = layoutGraph(nodes, 'brain/index.md');
    assert.equal(layout.size, nodes.length);
    assert.equal(layout.get('brain/index.md').root, true);
    for (const point of layout.values()) assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y));
    assert.deepEqual(layout, layoutGraph(nodes, 'brain/index.md'));
    assert.equal(layoutGraph([], 'missing').size, 0);
});

test('graph separates readable note cards when one research branch is much larger', () => {
    const nodes = [{ id: 'brain/index.md', branch: 'root' }];
    for (const [branch, size] of [['architecture', 3], ['research', 8], ['workflows', 4], ['tasks', 2]]) {
        for (let index = 0; index < size; index++) nodes.push({ id: `brain/${branch}/${index ? `note-${index}` : 'index'}.md`, branch });
    }
    const points = [...layoutGraph(nodes, 'brain/index.md').values()];
    for (let i = 0; i < points.length; i++) for (let j = i + 1; j < points.length; j++) {
        assert.ok(Math.abs(points[i].x - points[j].x) >= 148 || Math.abs(points[i].y - points[j].y) >= 52, `Cards ${i}, ${j} overlap`);
    }
});
