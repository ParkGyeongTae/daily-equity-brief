import { defineConfig } from 'vitepress'
import { briefSidebar } from './briefs.mjs'

export default defineConfig({
  title: 'Daily Equity Brief',
  description: '매일 한 종목씩 쓰는 개인 주식 리서치 브리프 — 선정은 뉴스, 근거는 1차 공시',
  lang: 'ko-KR',

  // GitHub Pages 프로젝트 페이지: https://parkgyeongtae.github.io/daily-equity-brief/
  base: '/daily-equity-brief/',

  // srcDir는 레포 루트. briefs/YYYY-MM-DD-*.md 경로 규칙(AGENTS.md)을 그대로 쓴다.
  srcExclude: ['README.md'],
  cleanUrls: true,
  lastUpdated: true,

  themeConfig: {
    nav: [
      { text: '브리프', link: '/briefs/' },
      { text: '방법론', link: '/AGENTS' },
      { text: 'Claude Code 모범 사례', link: '/claude-code-best-practices' },
    ],

    sidebar: {
      '/briefs/': [{ text: '브리프', items: briefSidebar() }],
      '/': [
        {
          text: '이 저장소',
          items: [
            { text: '소개', link: '/' },
            { text: '브리프 목록', link: '/briefs/' },
            { text: '방법론 (AGENTS.md)', link: '/AGENTS' },
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
