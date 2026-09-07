# Design QA

final result: blocked

## Visual evidence

- Source visual truth: user-attached login annotation and `reference/store.png` (original 2988 × 1974 px, including browser chrome).
- Login annotation reports a 972 × 789 CSS viewport; supplied image is 1280 × 1040 px. Blue selection rectangle and comment marker are annotation UI and intentionally omitted.
- Store reference pixel density and content-only CSS viewport are not independently measured. Browser chrome and speech overlay are excluded from implementation.
- Source public markup, CSS, catalog and original image assets were also read. Local asset provenance is in `reference/assets.md`.
- Implementation URLs: http://localhost:3000/ and http://localhost:3000/login.
- Implementation screenshot: unavailable. CUA repeatedly returns `Sky Computer Use service startup request failed`.
- Full-view and focused-region comparison: blocked; no local browser screenshot is available.

## Required fidelity surfaces

- Fonts/typography: source system font stack, teal eyebrows and hierarchy implemented. Visual verification pending.
- Spacing/layout: source header, centered 520 px login card, sidebar and three-column desktop catalog implemented. Responsive CSS supplied; desktop/mobile visual QA remains unverified.
- Colors/tokens: source colors, borders, shadows and control backgrounds applied. Pixel-level comparison pending.
- Assets: source logo, QQ image and eight brand/product images downloaded locally, with no source hotlinks. Phosphor line icons replace comparable source UI icons; exact icon paths are not replicated.
- Copy/content: login/store text grounded in reference. Counts reflect local data. Mock lottery intentionally uses an immediate result and an explicit coupon prize instead of the reference's scheduled three-slot drawing. Other member pages follow the same design language and authorized functionality; they are not claimed pixel-identical to unseen originals.

## Functional evidence

- Next.js production build: passed.
- TypeScript check: passed.
- 30 isolated HTTP integration tests: passed, using a temporary PostgreSQL schema.
- Frontend proxy integration: passed for registration, cookie session, order creation, mock payment, ticket persistence, logout and session invalidation.
- Server-rendered homepage contains 24 crawlable product links and Chinese page content.
- Product SSR includes Product JSON-LD with backend price and correct canonical URL.
- Mock defaults to noindex, nofollow, robots disallow and empty sitemap.
- Browser primary interactions and console errors: not checked due to unavailable browser control. HTTP integration is not substituted for visual verification.

## Findings

1. Blocking evidence gap: capture local desktop/mobile pages, compare reference and implementation together and fix P0/P1/P2 mismatches.
2. Source gap: registration, product detail and member screens lack source screenshots. Their functional implementations cannot be described as exact visual replicas.
3. Intentional mock boundaries: email code is shown locally; payments/recharges issue no real transactions; deliveries are mock cards; support has no external dispatch.
4. Agent/referral navigation opens explicit information pages; commission settlement is outside this implementation.

## Comparison history

- Original URL capture was blocked by browser startup failure.
- User supplied login/store screenshots, allowing implementation from images.
- Public source and asset retrieval later succeeded.
- No visual comparison pass has been completed; no visual acceptance is claimed.

## Implementation checklist

- [x] Build frontend/backend together with persistent local mock commerce.
- [x] Verify business rules and frontend/API integration.
- [x] Verify SSR content and metadata.
- [ ] Capture and compare desktop/mobile implementation.
- [ ] Test browser interactions and console.
- [ ] Obtain member-screen references for complete one-to-one visual fidelity.

## 2026-09-07 scoped checkout update

User supplied a checkout screenshot and requested variant selection, guest email input, automatic session email for signed-in users, quantity controls, and payment methods. These are implemented with backend variant inventory and order payment metadata. Register no longer requests verification, and agent/referral navigation has been removed per user instruction. This scoped change has API/type/build verification; browser visual comparison remains blocked.

## Guest checkout and support update

Source images: reference/order-flow/ (six user screenshots). Added QR dialog, created/paid receipt, public member/guest/cache lookup, ticket records with server-side search/pagination, and priority-based ticket creation. Mock QR replaces the original merchant QR intentionally. API integration, mail worker and build are verified. Browser visual verification is still blocked by CUA startup failure.

## Account/profile/recharge update

The user supplied profile/recent-purchase, account-menu and recharge references. Implemented grouped personal navigation, two-column profile information, recent-purchase table, profile and password subpages, custom recharge amount/payment selection and confirmation. Type/build and backend tests passed. Pixel comparison remains unverified because browser control is unavailable.

## 后台管理验收（2026-09-07）

- 新增 `/admin` 独立管理界面，采用商城品牌色和独立侧栏；后台隐藏商城页头/页脚/客服组件。
- 使用临时隔离 PostgreSQL schema 与测试账号，浏览器实际验证概览统计、成交趋势、商品分页列表、规格编辑弹窗，以及保存成功后返回列表。
- 发现并修复统计字段别名导致的概览 500 错误；新增统计金额/数量变化的集成回归测试。
- 修复隐藏商城页头后残留的 72px 顶部留白，侧栏提供独立滚动以支持较矮屏幕。
- 38 项集成测试、TypeScript 与生产构建通过。未逐个浏览器操作验证全部模块；已在 390×844 浏览器视口检查商品列表，移动端导航和表格支持横向滚动；未进行真实移动设备实测。
- 测试过程未为开发数据库中的普通用户提升权限；指定邮箱 `piusgo@plus.com` 尚未注册，因此管理员开通等待账号注册。


## 商品发布验收（2026-09-07）

- 在隔离数据库的生产构建预览中，从后台新增入口实际填写并上架「验收促销上架」；添加标准规格 ¥29.90 / 8 件和年卡 ¥199.00 / 3 件，后台汇总为到手价 ¥29.90、库存 11。
- 返回前台后搜索「验收」，确认促销商品与后台创建的抽奖商品都出现在同一个商品列表中，名称、图片、标签、到手价、库存正确显示，不再附加硬编码抽奖卡片。
- 打开新建商品详情并切换年卡，确认显示库存 3、应付金额 ¥199.00。
- 44 项隔离数据库测试、TypeScript、生产构建、迁移一致性检查均通过；图片上传和抽奖中奖原子性由自动化测试验证，未通过浏览器执行真实交易或向真实开发商城添加验收商品。
