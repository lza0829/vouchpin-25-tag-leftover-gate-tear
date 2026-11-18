<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const d = ref(null)
const err = ref('')
async function load() {
  err.value = ''
  try { d.value = await api.dashboard() } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div class="panel">
    <div class="grid" v-if="d">
      <div class="stat"><div class="k">业务日</div><div class="v">{{ d.business_day }}</div></div>
      <div class="stat"><div class="k">今日事件</div><div class="v">{{ d.event_count }}</div></div>
      <div class="stat"><div class="k">核销事实</div><div class="v ticket">{{ d.fact_count }}</div></div>
      <div class="stat"><div class="k">待钉 open</div><div class="v">{{ d.open_fact_count }}</div></div>
      <div class="stat"><div class="k">今日核销额</div><div class="v ticket">{{ d.redeem_total_today }}</div></div>
      <div class="stat"><div class="k">日闸</div><div class="v">{{ d.day_sealed ? '已关' : '未关' }}</div></div>
      <div class="stat"><div class="k">清单可钉</div><div class="v">{{ d.leftover_posted && d.leftover_posted.can_nail ? '是' : '否' }}</div></div>
    </div>
    <p class="msg" v-if="d && d.day_sealed">
      日闸已由批次 #{{ d.gate_batch_id }} 关闭<template v-if="d.gate_tag !== null">（首个钉扎 tag：<span class="tag">{{ d.gate_tag || '（空）' }}</span>）</template>；
      此日不得再写入核销，也不得再钉其它 tag。
      <template v-if="d.leftover_tags.length">批快照遗留 tag：<span class="tag warn" v-for="t in d.leftover_tags" :key="t">{{ t || '（空）' }}</span></template>
    </p>
    <p class="msg" v-else-if="d">子集钉也会关闭整日：首个 sealed 批之后，其它 tag 既不能核销也不能再钉。</p>
    <p class="msg err" v-if="err">{{ err }}</p>
  </div>
  <div class="panel" v-if="d">
    <h2 class="sub">按 tag 汇总</h2>
    <table>
      <thead><tr><th>tag</th><th>事实</th><th>open</th><th>sealed</th><th>voided</th><th>合计</th><th>今日已钉</th></tr></thead>
      <tbody>
        <tr v-for="r in d.tag_overview" :key="r.tag">
          <td><span class="tag">{{ r.tag || '（空）' }}</span></td>
          <td>{{ r.fact_count }}</td>
          <td>{{ r.open_count }}</td>
          <td>{{ r.sealed_count }}</td>
          <td>{{ r.voided_count }}</td>
          <td>{{ r.total_amount }}</td>
          <td>{{ r.sealed_today ? '是' : '否' }}</td>
        </tr>
        <tr v-if="!d.tag_overview.length"><td colspan="7" class="msg">当日暂无核销事实</td></tr>
      </tbody>
    </table>
  </div>
</template>
