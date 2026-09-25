#!/usr/bin/env node
/**
 * 빌드 산출물에서 base가 빠진 내부 절대 링크를 찾는다.
 *
 * VitePress는 마크다운 링크와 themeConfig의 링크에만 base('/daily-equity-brief/')를
 * 자동으로 붙인다. 데이터 로더가 만든 경로를 :href / :src 로 바인딩하면 base가 빠진 채
 * 렌더되고, 브라우저는 이를 도메인 루트 기준으로 풀어 404가 난다. 실제로 브리프 목록에서
 * 이 버그가 났다(fix(site) 6f2d537). 사이드바는 같은 경로를 themeConfig로 받아 base가
 * 붙으므로 증상이 한쪽에서만 나타나, 목록을 눌러보기 전에는 드러나지 않는다.
 *
 * 사용법:  node scripts/check_site_links.mjs [dist 경로]
 *   기본 경로는 .vitepress/dist. npm run docs:build 가 빌드 직후 이 스크립트를 부른다.
 *   위반이 있으면 파일·링크를 출력하고 exit 1 — CI의 build 잡이 그대로 실패한다.
 *
 * base는 설정 파일을 파싱하지 않고 dist/index.html 의 app 스크립트 경로에서 읽는다.
 * VitePress가 직접 base를 붙여 쓴 값이므로, config.mts 를 고쳐도 따라온다.
 *
 * 한계: SSR로 렌더된 HTML만 본다. 클릭 이후에야 만들어지는 링크는 여기에 남지 않으므로
 * 잡지 못한다. 이 검사는 "빌드 결과에 이미 박혀 있는" base 누락만 막는다.
 */

import fs from 'node:fs'
import path from 'node:path'

const dist = path.resolve(process.argv[2] ?? '.vitepress/dist')

if (!fs.existsSync(dist)) {
  console.error(`check_site_links: 빌드 산출물이 없다 — ${dist}`)
  process.exit(1)
}

// base 판정: dist/index.html 이 부르는 app 번들 경로의 앞부분.
const rootHtml = path.join(dist, 'index.html')
const appSrc = /<script[^>]+src="(\/[^"]*?\/?)assets\/app\.[^"]*\.js"/.exec(
  fs.readFileSync(rootHtml, 'utf-8'),
)
if (!appSrc) {
  console.error(`check_site_links: ${rootHtml} 에서 base를 읽지 못했다`)
  process.exit(1)
}
const base = appSrc[1]

// base가 '/' 이면 루트 배포라 이 검사가 잡을 것이 없다.
if (base === '/') {
  console.log('check_site_links: base가 "/" 이므로 검사를 건너뛴다')
  process.exit(0)
}

function htmlFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name)
    if (e.isDirectory()) return e.name === 'assets' ? [] : htmlFiles(full)
    return e.isFile() && e.name.endsWith('.html') ? [full] : []
  })
}

const ATTR_RE = /\b(?:href|src)="(\/[^"]*)"/g

const violations = []
for (const file of htmlFiles(dist)) {
  const html = fs.readFileSync(file, 'utf-8')
  for (const [, url] of html.matchAll(ATTR_RE)) {
    if (url.startsWith('//')) continue // 프로토콜 상대 경로 — 외부 링크다
    if (url.startsWith(base)) continue
    violations.push({ file: path.relative(dist, file), url })
  }
}

if (violations.length === 0) {
  console.log(`check_site_links: base "${base}" 누락 없음`)
  process.exit(0)
}

console.error(`check_site_links: base "${base}" 가 빠진 내부 링크 ${violations.length}건\n`)
for (const { file, url } of violations) {
  console.error(`  ${file}  →  ${url}`)
}
console.error(
  "\n데이터로 받은 경로를 :href / :src 로 바인딩했다면 vitepress 의 withBase() 로 감싼다.\n" +
    '  import { withBase } from "vitepress"\n' +
    '  <a :href="withBase(b.link)">',
)
process.exit(1)
