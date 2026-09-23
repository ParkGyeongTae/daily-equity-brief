---
title: 브리프
---

<script setup>
import { data as briefs } from './briefs.data.mts'
</script>

# 브리프

날짜 역순. 각 브리프의 모든 수치는 1차 공시 원문에서 확인한 것이며, 문서 하단 `출처`에서 원문 링크를 볼 수 있습니다.

<ul v-if="briefs.length" class="brief-list">
  <li v-for="b in briefs" :key="b.link">
    <a :href="b.link">
      <span class="brief-date">{{ b.date }}</span>
      <span class="brief-title">{{ b.title.replace(/\s*—\s*\d{4}-\d{2}-\d{2}\s*$/, '') }}</span>
    </a>
    <p v-if="b.summary" class="brief-summary">{{ b.summary }}</p>
  </li>
</ul>

<div v-else class="vp-doc">

아직 작성된 브리프가 없습니다. 첫 브리프는 `briefs/YYYY-MM-DD-<종목코드>.md` 로 추가하면 이 목록과 사이드바에 자동으로 나타납니다.

</div>

<style scoped>
.brief-list {
  list-style: none;
  padding: 0;
  margin: 2rem 0 0;
}
.brief-list li {
  padding: 1rem 0;
  border-top: 1px solid var(--vp-c-divider);
}
.brief-list li:last-child {
  border-bottom: 1px solid var(--vp-c-divider);
}
.brief-list a {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.25rem 0.75rem;
  text-decoration: none;
  font-weight: 600;
  color: var(--vp-c-text-1);
}
.brief-list a:hover .brief-title {
  color: var(--vp-c-brand-1);
}
.brief-date {
  flex: none;
  font-family: var(--vp-font-family-mono);
  font-size: 0.8125rem;
  font-weight: 400;
  color: var(--vp-c-text-3);
}
.brief-title {
  font-size: 1.0625rem;
  transition: color 0.2s;
}
.brief-summary {
  margin: 0.375rem 0 0;
  font-size: 0.875rem;
  line-height: 1.6;
  color: var(--vp-c-text-2);
}
</style>
