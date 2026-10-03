---
layout: home

hero:
  name: Daily Equity Brief
  text: 매일 한 종목
  tagline: 선정은 뉴스, 근거는 1차 공시, 타이밍은 가격 데이터.
  actions:
    - theme: brand
      text: 브리프 보기
      link: /briefs/
    - theme: alt
      text: 조건 원장
      link: /briefs/ledger
    - theme: alt
      text: 방법론
      link: /AGENTS
---

<script setup>
// 기본 테마의 features 카드는 본문(Content)보다 앞에 그려진다. 매일 들어오는 사람이 먼저 볼 것은
// 원칙이 아니라 최근 브리프이므로, 카드를 프런트매터가 아니라 본문에 두어 순서를 바꾼다.
// withBase: 데이터로 받은 경로를 :href로 바인딩하면 base가 빠진다(briefs/index.md 주석 참고).
import { withBase } from 'vitepress'
import { data as briefs } from './briefs/briefs.data.mts'

const recent = briefs.slice(0, 3)
const shortTitle = (t) => t.replace(/\s*—\s*\d{4}-\d{2}-\d{2}\s*$/, '')
</script>

## 최근 브리프

<ul v-if="recent.length" class="home-briefs">
  <li v-for="b in recent" :key="b.link">
    <a :href="withBase(b.link)">
      <span class="home-briefs-date">{{ b.date }}</span>
      <span class="home-briefs-title">{{ shortTitle(b.title) }}</span>
    </a>
    <p v-if="b.summary" class="home-briefs-summary">{{ b.summary }}</p>
  </li>
</ul>
<p v-else>아직 작성된 브리프가 없습니다.</p>

<p class="home-briefs-more"><a :href="withBase('/briefs/')">전체 목록 →</a></p>

## 원칙

<div class="home-principles">
  <div class="home-principle">
    <h3>뉴스는 질문, 공시가 답</h3>
    <p>뉴스는 종목을 고르는 데만 씁니다. 모든 숫자는 DART·SEC EDGAR 원문에서 확인한 뒤 그 공시를 출처로 적고, 모르는 것은 "확인 불가"로 남깁니다.</p>
  </div>
  <div class="home-principle">
    <h3>예측이 아니라 조건</h3>
    <p>"오를 것이다"를 쓰지 않습니다. 무엇이 관찰되면 진입하고 무엇이 관찰되면 무효인지를 적고, 진입 조건에는 항상 같은 정밀도의 무효화 조건이 따라붙습니다.</p>
  </div>
  <div class="home-principle">
    <h3>조건은 기록된다</h3>
    <p>브리프에 적은 가격 조건이 그 뒤 종가로 관찰됐는지 <a :href="withBase('/briefs/ledger')">조건 원장</a>에 대조해 남깁니다. 성과표가 아니라, 내가 쓴 조건이 관찰됐는가의 기록입니다.</p>
  </div>
</div>

<style scoped>
.home-briefs {
  list-style: none;
  padding: 0;
  margin: 1rem 0 0;
}
.home-briefs li {
  margin: 0;
  padding: 1rem 0;
  border-top: 1px solid var(--vp-c-divider);
}
.home-briefs li:last-child {
  border-bottom: 1px solid var(--vp-c-divider);
}
.home-briefs a {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.25rem 0.75rem;
  text-decoration: none;
  font-weight: 600;
  color: var(--vp-c-text-1);
}
.home-briefs a:hover .home-briefs-title {
  color: var(--vp-c-brand-1);
}
.home-briefs-date {
  flex: none;
  font-family: var(--vp-font-family-mono);
  font-size: 0.8125rem;
  font-weight: 400;
  color: var(--vp-c-text-3);
}
.home-briefs-title {
  font-size: 1.0625rem;
  transition: color 0.2s;
}
.home-briefs-summary {
  margin: 0.375rem 0 0;
  font-size: 0.875rem;
  line-height: 1.6;
  color: var(--vp-c-text-2);
}
.home-briefs-more {
  margin-top: 0.75rem;
  font-size: 0.875rem;
}
.home-principles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1rem;
  margin-top: 1rem;
}
.home-principle {
  padding: 1.25rem 1.5rem;
  border: 1px solid var(--vp-c-bg-soft);
  border-radius: 12px;
  background-color: var(--vp-c-bg-soft);
}
.home-principle h3 {
  margin: 0;
  font-size: 1rem;
  line-height: 1.5;
}
.home-principle p {
  margin: 0.5rem 0 0;
  font-size: 0.875rem;
  line-height: 1.6;
  color: var(--vp-c-text-2);
}
</style>
