import Link from 'next/link';
import {notFound} from 'next/navigation';
const content:Record<string,{title:string;intro:string;sections:{title:string;text:string}[]}>= {
  tutorial:{title:'使用教程',intro:'从选购到交付，四步完成数字服务购买。',sections:[{title:'1. 注册与登录',text:'填写邮箱、密码和确认密码即可注册，无需验证码。登录时需填写图片中的四位验证码，找回密码仍需邮箱验证码。'},{title:'2. 选择商品',text:'通过商品分类或搜索找到需要的服务。进入详情页后填写数量、接收邮箱或账号，选择可用优惠券。'},{title:'3. 提交订单与付款',text:'提交订单后进入「查订单」。可选择模拟支付，或先模拟充值再使用演示余额付款，不会产生真实扣款。'},{title:'4. 查看交付与售后',text:'支付成功后，订单中显示演示卡密。需要帮助时，进入工单中心并关联你的订单。'}]},
  announcements:{title:'平台公告',intro:'服务动态与平台使用说明。',sections:[{title:'本地演示版本已开放',text:'当前可测试注册、登录、商品选购、下单、模拟付款、查单及工单提交。所有商品价格、库存、优惠规则和交付内容均为 mock 数据。'},{title:'新人优惠与免费抽奖',text:'登录后可领取一次满 100 元减 10 元优惠券。指定商品抽奖每日可免费参与一次，中奖结果及优惠券会保存在你的账号中。'}]},
  agent:{title:'成为代理',intro:'代理功能介绍。',sections:[{title:'代理服务尚未开放',text:'本期优先实现会员与购物流程。代理等级、供货价格和代理申请尚未接入，暂不接受付费升级。你可以通过工单提出需求。'}]},
  referral:{title:'推广领佣金',intro:'推广服务介绍。',sections:[{title:'推广佣金尚未开放',text:'当前演示版未启用邀请关系、佣金结算和提现功能。不会计算或发放真实佣金。后续可以在现有账号与订单接口上扩展。'}]},
  terms:{title:'平台说明',intro:'了解当前演示网站的功能范围。',sections:[{title:'演示环境',text:'本网站为独立开发的本地演示。账号与参考网站不互通；测试数据仅保存在本地数据库。请勿使用重要密码或填写第三方平台的账号密码。'},{title:'支付与交付',text:'模拟充值、模拟支付与演示卡密仅用于验证购物流程，不产生真实交易，也不代表任何第三方平台已提供服务。'},{title:'帮助与反馈',text:'工单会保存在本地账户中。当前没有真实客服值守，也不会向参考网站发送消息。'}]},
};
type Props={params:Promise<{slug:string}>};
export async function generateMetadata({params}:Props){const c=content[(await params).slug];return {title:c?.title||'页面不存在',robots:{index:false,follow:false}};}
export default async function Guide({params}:Props){const c=content[(await params).slug];if(!c)notFound();return <main id="main" className="site-main"><article className="panel prose"><span className="eyebrow">HELP CENTER</span><h1>{c.title}</h1><p>{c.intro}</p>{c.sections.map(s=><section key={s.title}><h2>{s.title}</h2><p>{s.text}</p></section>)}<Link className="secondary" href="/tickets">提交工单</Link></article></main>;}
