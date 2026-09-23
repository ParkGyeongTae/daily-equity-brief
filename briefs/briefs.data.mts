// VitePress 데이터 로더 — briefs/index.md 에서 data로 받는다.
// @ts-expect-error .mjs 모듈 타입 선언 없음
import { readBriefs } from '../.vitepress/briefs.mjs'

export default {
  watch: ['./*.md'],
  load() {
    return readBriefs()
  },
}
