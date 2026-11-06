// Validates: REQ-HUB-008. Render the real route components from a shared URL.
const assert = require('node:assert/strict')
const path = require('node:path')
const { buildSync } = require('esbuild')
const frontend = path.resolve(__dirname, '..')
const bundle = buildSync({
  stdin: {
    contents: `import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server';
      import {MemoryRouter} from 'react-router-dom';
      import {AuthProvider} from './src/auth';
      import Search from './src/pages/Search'; import Documents from './src/pages/Documents';
      import {readFilters, writeFilters, useQuerySort} from './src/queryState';
      export {readFilters, writeFilters};
      function Sorted() { const {sort, sorted} = useQuerySort([{name:'V1.0.10'}, {name:null}, {name:'V1.0.9'}], [{key:'name',value:r=>r.name}]); return <output>{JSON.stringify({sort, sorted})}</output>; }
      export function renderSort(url) { return renderToStaticMarkup(<MemoryRouter initialEntries={[url]}><Sorted/></MemoryRouter>); }
      export function render(route, url) { return renderToStaticMarkup(<AuthProvider><MemoryRouter initialEntries={[url]}>{route === 'search' ? <Search/> : <Documents/>}</MemoryRouter></AuthProvider>); }`,
    resolveDir: frontend, loader: 'tsx',
  },
  bundle: true, platform: 'node', format: 'cjs', write: false, jsx: 'automatic',
  define: { 'import.meta.env.BASE_URL': '"/"' },
})
const compiled = new module.constructor()
compiled._compile(bundle.outputFiles[0].text, path.join(frontend, 'route-check.cjs'))
const { render, renderSort, readFilters, writeFilters } = compiled.exports
const search = render('search', '/search?q=shared&document_number=DOC-7&current_only=true&view=advanced')
assert.match(search, /value="shared"/, 'Search must restore the shared URL query')
assert.match(search, /value="DOC-7"/, 'Search must restore advanced filters')
assert.match(search, /checked=""/, 'Search must restore Current-only')
assert.match(search, /최대 200건까지 표시합니다/, 'Search must disclose the result limit')
const documents = render('documents', '/documents?q=Guide&status=archived')
assert.match(documents, /value="Guide"/, 'Documents must restore query')
assert.match(documents, /value="archived" selected=""/, 'Documents must restore status')
const defaults = {q:'', product_id:'', current_only:false, document_status:'active'}
const original = new URLSearchParams('q=한글+%26+PDF&product_id=p1&current_only=true&document_status=all&sort=document&dir=desc&view=advanced')
const restored = readFilters(original, defaults)
assert.deepEqual(restored, {q:'한글 & PDF', product_id:'p1', current_only:true, document_status:'all'})
const saved = writeFilters(original, {...restored, q:'두 번째 검색'}, defaults)
assert.equal(readFilters(saved, defaults).q, '두 번째 검색')
assert.equal(saved.get('sort'), 'document')
assert.equal(saved.get('view'), 'advanced')
assert.equal(readFilters(original, defaults).q, '한글 & PDF', 'Back navigation must retain the previous URL values')
assert.equal(writeFilters(saved, defaults, defaults).get('q'), null)
assert.equal(readFilters(new URLSearchParams('current_only=false'), defaults).current_only, false)
const decodeSort = (url) => JSON.parse(renderSort(url).replace(/<\/?output>/g, '').replaceAll('&quot;', '"'))
assert.deepEqual(decodeSort('/search?sort=name&dir=desc').sorted.map(r=>r.name), ['V1.0.10','V1.0.9',null])
assert.deepEqual(decodeSort('/search?sort=unknown&dir=invalid').sorted.map(r=>r.name), ['V1.0.9','V1.0.10',null])
console.log('공유 URL 화면 검사 통과')
