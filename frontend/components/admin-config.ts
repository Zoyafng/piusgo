export type Row = Record<string, string | number | null>;
export type Module = {key:string;label:string;symbol:string;description:string;columns:string[];filters?:[string,string][]};
export const modules:Module[]=[
  {key:'overview',label:'数据概览',symbol:'◫',description:'在一个地方，掌握商城的每一笔业务。',columns:[]},
  {key:'users',label:'用户管理',symbol:'◎',description:'查看会员资料、余额与登录记录，管理账号状态。',columns:['email','display_name','balance','role','disabled','created'],filters:[['0','正常'],['1','已禁用']]},
  {key:'orders',label:'订单管理',symbol:'▤',description:'统一查看会员与游客订单，跟进付款和交付。',columns:['id','source','account','variant_name','total','status','created'],filters:[['pending','待付款'],['paid','已付款'],['cancelled','已关闭']]},
  {key:'products',label:'商品管理',symbol:'◇',description:'发布促销与抽奖商品，统一管理规格库存和活动优惠券。',columns:['id','name','product_type','price','stock','coupon_eligible','active'],filters:[['1','已上架'],['0','已下架']]},
  {key:'tickets',label:'售后工单',symbol:'☷',description:'处理售后问题，回复内容会同步显示在用户工单中心。',columns:['title','order_id','priority','status','created'],filters:[['open','待回复'],['waiting_user','待用户回复'],['resolved','已解决'],['closed','已关闭']]},
  {key:'coupons',label:'优惠券',symbol:'▱',description:'查看优惠券归属与使用情况，可停用未使用的优惠券。',columns:['id','user_id','kind','amount','minimum','used'],filters:[['0','可用'],['1','已使用 / 停用']]},
  {key:'ledger',label:'充值流水',symbol:'↗',description:'查看已入账充值与赠送金额，保留原始财务记录。',columns:['id','user_id','amount','bonus','payment_method','created']},
  {key:'lottery',label:'抽奖记录',symbol:'✧',description:'查看活动参与和中奖记录。',columns:['user_id','product_id','order_id','campaign','won','created'],filters:[['1','中奖'],['0','未中奖']]},
  {key:'mail',label:'邮件投递',symbol:'✉',description:'跟进交付邮件状态，失败任务可以重新排队投递。',columns:['recipient','order_id','status','attempts','created'],filters:[['queued','排队中'],['sending','发送中'],['sent','已发送'],['mock_delivered','模拟投递'],['failed','失败']]},
  {key:'pages',label:'内容管理',symbol:'▧',description:'维护公告、使用教程和平台说明，保存后同步到前台。',columns:['id','title','intro']},
  {key:'audit',label:'操作日志',symbol:'◷',description:'追踪管理员操作和业务事件。日志只读保留。',columns:['event','user_id','object_id','created']},
];
export const labels:Record<string,string>={source:'订单来源',variant_id:'规格编号',product_type:'商品类型',coupon_eligible:'活动优惠券',intro:'简介',sections:'段落',id:'编号',email:'邮箱',display_name:'昵称',phone:'联系电话',role:'角色',disabled:'账号状态',balance:'账户余额',created:'创建时间',last_login_at:'最近登录时间',last_login_ip:'最近登录 IP',previous_login_at:'上次登录时间',previous_login_ip:'上次登录 IP',category:'分类编号',name:'商品名称',image:'图片地址',price:'售价',stock:'库存',badge:'角标',tags:'标签',active:'上架状态',user_id:'用户编号',product_id:'商品编号',quantity:'数量',account:'接收邮箱',total:'实付 / 应付金额',discount:'优惠金额',status:'状态',paid_at:'付款时间',variant_name:'规格',payment_method:'支付方式',delivery:'交付内容',order_id:'订单编号',title:'工单标题',body:'问题详情',priority:'优先级',reply:'客服回复',replied_at:'回复时间',kind:'优惠类型',amount:'金额',minimum:'使用门槛',used:'使用状态',bonus:'赠送金额',campaign:'活动',won:'抽奖结果',recipient:'收件邮箱',attempts:'尝试次数',sent_at:'投递时间',error:'错误原因',event:'事件',object_id:'操作对象',order_count:'订单总数'};
export const states:Record<string,string>={pending:'待付款',paid:'已付款',cancelled:'已关闭',open:'待回复',waiting_user:'待用户回复',resolved:'已解决',closed:'已关闭',queued:'排队中',sending:'发送中',sent:'已发送',mock_delivered:'模拟投递',failed:'失败',low:'低',medium:'中',high:'高',member:'普通会员',admin:'管理员',mock:'模拟支付',alipay:'支付宝',wechat:'微信',balance:'余额',newcomer:'新人优惠'};
