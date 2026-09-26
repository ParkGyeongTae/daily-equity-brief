import { defineConfig } from 'vitepress'
import { briefSidebar } from './briefs.mjs'

// GitHub Pages 프로젝트 페이지 경로. head의 링크는 VitePress가 base를 붙여주지 않으므로
// 아래 head에서 이 상수를 직접 끼워 쓴다.
const base = '/daily-equity-brief/'

export default defineConfig({
  title: 'Daily Equity Brief',
  description: '매일 한 종목씩 쓰는 개인 주식 리서치 브리프 — 선정은 뉴스, 근거는 1차 공시',
  lang: 'ko-KR',

  // GitHub Pages 프로젝트 페이지: https://parkgyeongtae.github.io/daily-equity-brief/
  base,

  // 파비콘. 원본은 public/favicon.svg이고 ico/png는 같은 도형을 래스터로 다시 그린 것이다.
  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: `${base}favicon.svg` }],
    ['link', { rel: 'alternate icon', type: 'image/x-icon', href: `${base}favicon.ico` }],
    ['link', { rel: 'apple-touch-icon', sizes: '180x180', href: `${base}apple-touch-icon.png` }],
    ['meta', { name: 'theme-color', content: '#3451b2' }],
  ],

  // srcDir는 레포 루트. briefs/YYYY-MM-DD-*.md 경로 규칙(AGENTS.md)을 그대로 쓴다.
  // CLAUDE.md는 AGENTS.md 심볼릭 링크 — 중복 페이지가 생기지 않게 제외한다.
  // workflow.md는 .claude/skills/daily-brief/SKILL.md 심볼릭 링크다. VitePress가 점(.)으로
  // 시작하는 디렉터리를 훑지 않으므로, 스킬 문서를 사이트에 싣는 통로로 심볼릭 링크를 쓴다.
  srcExclude: ['README.md', 'CLAUDE.md'],
  cleanUrls: true,
  lastUpdated: true,

  themeConfig: {
    nav: [
      { text: '브리프', link: '/briefs/' },
      { text: '조건 원장', link: '/briefs/ledger' },
      { text: '방법론', link: '/AGENTS' },
      { text: '작성 절차', link: '/workflow' },
      { text: 'Claude Code 모범 사례', link: '/claude-code-best-practices' },
    ],

    sidebar: {
      '/briefs/': [
        // 원장은 브리프가 아니라 브리프들에 대한 기록이므로 목록 위에 따로 둔다.
        { text: '기록', items: [{ text: '조건 원장', link: '/briefs/ledger' }] },
        { text: '브리프', items: briefSidebar() },
      ],
      '/': [
        {
          text: '이 저장소',
          items: [
            { text: '소개', link: '/' },
            { text: '브리프 목록', link: '/briefs/' },
            { text: '조건 원장', link: '/briefs/ledger' },
            { text: '방법론 (AGENTS.md)', link: '/AGENTS' },
            { text: '작성 절차 (daily-brief 스킬)', link: '/workflow' },
            { text: 'Claude Code 모범 사례', link: '/claude-code-best-practices' },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/ParkGyeongTae/daily-equity-brief' },
    ],

    outline: { level: [2, 3], label: '목차' },
    docFooter: { prev: '이전', next: '다음' },
    lastUpdatedText: '마지막 수정',
    darkModeSwitchLabel: '테마',
    returnToTopLabel: '위로',
    sidebarMenuLabel: '메뉴',

    search: {
      provider: 'local',
      options: {
        translations: {
          button: { buttonText: '검색', buttonAriaLabel: '검색' },
          modal: {
            noResultsText: '검색 결과가 없습니다',
            resetButtonTitle: '검색어 지우기',
            footer: { selectText: '선택', navigateText: '이동', closeText: '닫기' },
          },
        },
      },
    },

    footer: {
      message: '본 사이트는 개인 학습용 기록이며 투자 권유가 아닙니다.',
      copyright: '© ParkGyeongTae',
    },
  },
})
