// briefs/ 디렉터리를 읽어 브리프 목록을 만든다.
// 파일명 규칙(AGENTS.md): briefs/YYYY-MM-DD-<종목코드 또는 티커>.md
// 제목은 본문 첫 h1에서, 한 줄 요약은 첫 인용문(> ...)에서 뽑는다.
// 프런트매터를 요구하지 않으므로 AGENTS.md의 브리프 템플릿을 고칠 필요가 없다.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BRIEFS_DIR = path.resolve(fileURLToPath(new URL('.', import.meta.url)), '../briefs')
const FILE_RE = /^(\d{4}-\d{2}-\d{2})-(.+)\.md$/

export function readBriefs() {
  let files = []
  try {
    files = fs.readdirSync(BRIEFS_DIR)
  } catch {
    return [] // briefs/ 가 아직 없으면 빈 목록
  }

  return files
    .map((file) => {
      const m = FILE_RE.exec(file)
      if (!m) return null // index.md, 규칙에 안 맞는 파일은 제외

      const [, date, code] = m
      const raw = fs.readFileSync(path.join(BRIEFS_DIR, file), 'utf-8')
      const body = raw.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '') // 프런트매터가 있으면 제거

      const h1 = /^#\s+(.+)$/m.exec(body)
      const quote = /^>\s*(?:한 줄 요약\s*[:：]\s*)?(.+)$/m.exec(body)

      return {
        date,
        code,
        title: h1 ? h1[1].trim() : `${date} ${code}`,
        summary: quote ? quote[1].trim() : '',
        link: `/briefs/${file.replace(/\.md$/, '')}`,
      }
    })
    .filter(Boolean)
    .sort((a, b) => (a.date === b.date ? b.code.localeCompare(a.code) : b.date.localeCompare(a.date)))
}

// 사이드바용: 날짜 역순, "2026-09-23 · 삼성전자 (005930)" 형태
export function briefSidebar() {
  const briefs = readBriefs()
  if (briefs.length === 0) return [{ text: '아직 작성된 브리프가 없습니다', link: '/briefs/' }]

  return briefs.map(({ date, title, link }) => ({
    text: `${date} · ${title.replace(/\s*—\s*\d{4}-\d{2}-\d{2}\s*$/, '')}`,
    link,
  }))
}
