// eval/assert_case.js —— 业务判据的断言实现（promptfoo javascript assertion）
//
// 它只做一件事：把 case_server.py 返回的那份 JSON，按 assertion 的 config.check 翻译成 pass/fail。
// ★ 判分口径不在这里 —— Recall@5 与 knowledge_hit 都是服务端用 evalset/run_eval.py 的
//   同一个实现算好的；这里再算一遍就是第二把尺子。
//
// output 可能是已解析的对象（Content-Type: application/json 时）也可能是原始文本，两种都收。
module.exports = (output, context) => {
  let j = output;
  if (typeof j === 'string') {
    try { j = JSON.parse(j); } catch (e) {
      return { pass: false, score: 0, reason: '返回不是 JSON：' + String(output).slice(0, 120) };
    }
  }
  if (!j || typeof j !== 'object') {
    return { pass: false, score: 0, reason: '返回为空' };
  }
  if (j.error) {
    return { pass: false, score: 0, reason: '服务端报错：' + String(j.error).slice(0, 200) };
  }
  // ★ 题面漂移必须报红：tests.yaml 是由 questions.json 生成的，两边不一致说明
  //   现在跑的不是"那 105 题" —— 这时任何分数都是假的。
  if (j.drift) {
    return { pass: false, score: 0, reason: 'tests.yaml 的题面与 heldout/questions.json 不一致（qid=' + j.qid + '），请重跑 gen_tests.py' };
  }

  const check = (context.config && context.config.check) || '';
  const cands = (j.cands || []).map(c => c.doc + '#' + (c.clause || '?')).join(' | ');

  if (check === 'recall5') {
    const ok = j.gold_hit === true;
    return { pass: ok, score: ok ? 1 : 0,
             reason: 'gold_hit=' + j.gold_hit + ' gold_rank=' + j.gold_rank + ' 候选=' + cands };
  }
  if (check === 'no_miss') {
    const ok = j.knowledge_hit === true;
    return { pass: ok, score: ok ? 1 : 0,
             reason: 'knowledge_hit=' + j.knowledge_hit + '（应答题被拒 = 漏答）' };
  }
  if (check === 'refuse_ok') {
    const ok = j.knowledge_hit === false;
    return { pass: ok, score: ok ? 1 : 0,
             reason: 'knowledge_hit=' + j.knowledge_hit + '（拒答题被答 = 误答）答案前 60 字：' + String(j.answer || '').slice(0, 60) };
  }
  return { pass: false, score: 0, reason: '未知 check：' + check };
};
