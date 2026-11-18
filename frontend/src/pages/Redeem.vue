<script setup>
import { onMounted, ref, watch } from 'vue'
import { api } from '../api'

const TAG_ALL = '__all__'
const TAG_EMPTY = '__empty__'
const preview = ref(null)
const choice = ref(TAG_ALL)
const leftoverAction = ref('carry')
const msg = ref('')
const err = ref('')

function queryTag() {
  if (choice.value === TAG_ALL) return undefined
  if (choice.value === TAG_EMPTY) return ''
  return choice.value
}

async function load() {
  err.value = ''
  try {
    preview.value = await api.preview(undefined, queryTag())
  } catch (e) { err.value = e.message }
}
watch(choice, load)
async function seal() {
  msg.value = ''; err.value = ''
  try {
    const t = queryTag()
    const body = { tag: t === undefined ? null : t }
    if (preview.value?.requires_leftover_action) body.leftover_action = leftoverAction.value
    const r = await api.seal(body)
    const closure = r.leftover_closure || {}
    let extra = ''
    if (closure.count) {
      extra = closure.action === 'carry'
        ? ` · 遗留 ${closure.count} 笔改挂 ${closure.target_day}`
        : ` · 遗留 ${closure.count} 笔已作废`
    }
    msg.value = `已钉扎批次 #${r.batch_id} · tag ${r.tag === null ? '全部' : (r.tag || '（空）')} · ${r.fact_count} 笔 · 合计 ${r.total_amount}${extra}；日闸已关`
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div class="panel" v-if="preview">
    <div class="row">
      <label>按 tag 子集钉
        <select v-model="choice">
          <option :value="TAG_ALL">全部 tag（整日）</option>
          <option v-for="t in preview.tag_overview.filter((x) => x.tag !== '')" :key="t.tag" :value="t.tag">{{ t.tag }}</option>
          <option :value="TAG_EMPTY">（空 tag）</option>
        </select>
      </label>
    </div>
    <div class="grid">
      <div class="stat"><div class="k">业务日</div><div class="v">{{ preview.business_day }}</div></div>
      <div class="stat"><div class="k">本批待钉</div><div class="v ticket">{{ preview.open_count }}</div></div>
      <div class="stat"><div class="k">本批合计</div><div class="v ticket">{{ preview.open_total }}</div></div>
      <div class="stat"><div class="k">日闸</div><div class="v">{{ preview.gate.sealed ? '已关' : '未关' }}</div></div>
    </div>
    <p class="msg" v-if="preview.gate.sealed">
      本业务日已由批次 #{{ preview.gate.batch_id }} 关闸（tag <span class="tag">{{ preview.gate.tag === null ? '全部' : (preview.gate.tag || '（空）') }}</span>），不能再钉。
    </p>
    <div class="panel inset" v-if="preview.requires_leftover_action">
      <p class="msg err">
        本批将关闭整日，但仍有其它 tag 的 open 事实
        <span class="tag warn" v-for="t in preview.leftover_tags" :key="t">{{ t || '（空）' }}</span>
        （{{ preview.leftover_count }} 笔 / 合计 {{ preview.leftover_total }}）。关闸前必须收口。
      </p>
      <div class="row">
        <label>遗留收口
          <select v-model="leftoverAction">
            <option value="carry">改挂到下一未钉日</option>
            <option value="void">显式作废</option>
          </select>
        </label>
      </div>
    </div>
    <div class="row">
      <button type="button" @click="seal" :disabled="preview.gate.sealed || preview.open_count===0">钉扎本批并关日闸</button>
      <button type="button" class="ghost" @click="load">刷新预览</button>
    </div>
    <p class="msg" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
  </div>
  <div class="panel" v-if="preview">
    <table>
      <thead><tr><th>事实ID</th><th>券码</th><th>活动</th><th>tag</th><th>金额</th><th>状态</th></tr></thead>
      <tbody>
        <tr v-for="f in preview.facts" :key="f.id">
          <td>{{ f.id }}</td>
          <td>{{ f.coupon_code }}</td>
          <td>{{ f.campaign_code }}</td>
          <td><span class="tag">{{ f.tag || '（空）' }}</span></td>
          <td>{{ f.amount }}</td>
          <td><span class="tag warn">{{ f.status }}</span></td>
        </tr>
        <tr v-if="!preview.facts.length"><td colspan="6" class="msg">无符合条件的 open 事实</td></tr>
      </tbody>
    </table>
  </div>
</template>
