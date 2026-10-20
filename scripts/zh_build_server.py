"""Builds frontend/messages/server.zh.json: Simplified Chinese versions of the server's English reply and
clarification templates (data/lang/ne/replies.yaml and clarify.yaml). Machine-assisted translation, pending native review.
The UI shows them in zh-CN mode by matching the English template; chat understanding stays English and Nepali."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ZH = {
"replies": {
"help":"我可以显示您的余额和最近的转账，比较汇款路线，向您已保存的收款人转账，或支付待付账单。每一笔付款都需要您确认。",
"unsupported_intent":"这项功能暂未开放。目前我可以显示余额和记录、比较汇款路线、向已保存的收款人转账，以及支付待付账单。",
"unsupported_currency":"我无法处理该币种。支持的币种：NPR、AUD、QAR、MYR、CNY、USD。",
"attachments_unavailable":"暂不支持读取文件，请直接输入您的请求。",
"cancelled":"已取消，没有转出任何款项。",
"hash_mismatch":"确认内容与提案不一致，我已停止操作，没有转出任何款项。请重新开始。",
"executed":"已完成。已向 {payee} 转出 {amount}。您的最新余额为 {balance}。",
"insufficient_funds":"您的余额不足：当前为 {balance}，而此笔需要 {needed}。没有转出任何款项。",
"condition_not_met":"您设定的条件未满足：留出 {reserve} 作房租后，您将剩余 {left}，而此笔需要 {needed}。没有转出任何款项。",
"condition_unknown":"我无法从您的记录中得出房租金额，因此无法检查您的条件。没有转出任何款项。",
"no_route":"我找不到适合该金额的汇款路线：{reason}。没有转出任何款项。",
"planner_failed":"我无法为此制定安全的方案。请重试或换一种说法。",
"assistant_unavailable":"我现在无法回答这个问题。“汇款路线”“学费”“收款人”“家人”和“信用护照”按钮仍可使用。没有转出任何款项。",
"execution_failed":"我无法安全地完成此操作（{reason}）。没有转出任何款项。",
"sensitive_content":"请不要在对话中输入账号或密码等机密信息，请使用安全的“添加收款人”页面。",
"no_open_invoice":"您没有待付账单。",
"passport_none":"您还没有有效的信用护照，请先让我为您签发。",
"passport_issued":"您的信用护照已就绪，包含以下独立声明：{claims}。有效期至 {until}。由您选择分享哪些，目前尚未分享任何内容。",
"passport_shared":"已完成。给 {audience} 的证明只包含：{claims}。不含金额，也不含账号。请把下方的分享内容交给对方，对方可自行核验签名。",
"funding_infeasible":"我找不到能在 {due} 前付清 {owed} 的办法：您自己的账户加上家人能汇来的款项，无法及时凑够。没有做任何更改。",
"funding_plan_step":"第 {n} 步，共 {total} 步：请核对详情并确认。",
"contribution_requested":"我已请求 {amount}（将以 {arrives} 到账）。对方会在自己的账户中决定，没有从您这里扣款。",
"plan_done_waiting":"目前已支付：{paid}。家人的款项到账后仍需支付：{rest}，截止 {due}。",
"payee_saved_held":"已保存收款人 {account}。姓名取自文件，未经验证：{name}。在 {until} 之前不能向其付款，确认也不会改变这一点。",
"payee_saved_typed":"已保存收款人 {name}（{account}）。因为是新收款人，在 {until} 之前最多只能收到首笔限额。若今天需要多付，请银行核实该收款人。",
"proposal_ready":"请核对详情，并确认后发送。",
"read_result":"{lines}",
"already_processed":"这笔付款可能已经完成。再次尝试前，请先查看您的转账记录。",
"executed_fallback":"付款已完成，但我无法显示回执，请查看您的转账记录。",
"policy_denied":"我无法执行此操作。{reasons} 没有转出任何款项。",
"grant_failed":"您的确认已失效，没有转出任何款项。请重新发起并确认。",
"mfa_required":"这笔付款需要您的验证码，请输入 6 位数字验证码。",
"mfa_failed":"该验证码未被接受。转出功能被锁定前还剩 {left} 次机会。",
"locked":"为了您的安全，转出操作已被锁定。请输入验证码解锁。",
"document_received":"我已读取您的文件。{line} 您希望我如何处理它？",
"document_warning":"警告：此文件包含指令或被改动过的付款信息。我不会照做，任何基于它的付款都需要额外核查。",
"document_not_found":"我找不到该文件，请重新上传。",
"reader_failed":"我无法安全地读取它，因此没有使用。没有转出任何款项。",
"read_unavailable":"我无法给出确切数字。我只能陈述您账户中的数字和我刚查到的报价。",
},
"clarify": {
"amount":"您想转多少？",
"payee_ref":"收款人是谁？",
"recurrence":"需要多久重复一次（例如每月）？",
"currency_ambiguous":"您指的是哪种币种：{options}？",
"amount_ambiguous":"我发现不止一个金额（{options}），应该用哪一个？",
"payee_ref_ambiguous":"您指的是哪位收款人：{options}？",
"currency":"哪种币种？",
"deadline":"最晚需要什么时候到账？",
"deadline_ambiguous":"您指的是哪个日期：{options}？",
"payee_unknown":"{name} 是谁？我在您已保存的收款人中找不到。请说明您指的是谁，或在设置中添加。",
"payee_many":"您指的是哪一位：{options}？",
"deadline_past":"日期 {date} 已经过去或就是今天。请给出新日期，或回复“马上发送”（任何不带日期的回复均可），我将立即发送且不设截止日期。",
"invoice_which":"您想支付哪一张账单：{options}？",
"generic":"抱歉，我没有听懂。可以换一种说法再说一遍吗？",
"blocked_content":"请不要在对话中输入账号或密码等机密信息，请使用安全的“添加收款人”页面。",
"passport_claim":"我应该分享哪一项证明：{options}？请说出其中一项，例如房租。",
"paste_confirm":"您消息的一部分似乎来自他人，因此我不会使用其中的账号或金额。您指的是哪位已保存的收款人：{options}？请同时自己输入金额。",
"payee_account":"请把收款人的姓名和账号一起放在同一条消息里发送，例如：add Okafor Property, account 062000771234。我会把详情展示给您确认。",
"payee_name":"这位收款人应以什么名字保存？请把姓名和账号一起输入。",
},
}
out = []
for f in ("replies", "clarify"):
    d = yaml.safe_load((ROOT / "data" / "lang" / "ne" / f"{f}.yaml").read_text())["templates"]
    assert set(d) == set(ZH[f]), (f, set(d) ^ set(ZH[f]))
    for k, v in d.items():
        out.append({"id": f"{f}.{k}", "en": v["en"], "zh": ZH[f][k]})
(ROOT / "frontend" / "messages" / "server.zh.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
print(len(out), "templates")
