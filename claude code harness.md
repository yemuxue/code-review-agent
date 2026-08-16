
```Python
# =====================================================================
# 第一部分：类型与数据结构定义（数据契约层）
# =====================================================================

# 定义消息结构，代表对话中的一条记录（用户、助手或工具结果）
class Message:
    role: str          # 'user' | 'assistant' | 'tool_result
    content: any       # 文本或结构化内容
    tool_use_id: str   # 若为tool_result，关联到具体的工具调用ID

# 定义工具接口：模型能看到名称/参数，Harness管控权限与执行
class Tool:
    name: str          # 模型调用时使用的唯一名称
    input_schema: Schema  # 用于校验模型生成的参数（如 Zod/JSON Schema）
    
    # 以下方法由具体工具实现（如BashTool、ReadFileTool）
    def is_read_only(self, input): pass  # 是否只读（影响并发策略）
    def check_permissions(self, input, ctx): pass  # 返回 allow | deny | ask
    async def call(self, input, ctx, on_progress): pass  # 实际执行逻辑
    def map_result_to_block(self, output, tool_use_id): pass  # 转为模型可读的tool_result

# 静态输入参数：任务启动时确定，整个生命周期内不变
class QueryParams:
    messages: list[Message]          # 当前会话的历史消息
    system_prompt: str               # 系统级指令（角色、规则、边界）
    tools: list[Tool]                # 本次任务可用的全部工具池
    can_use_tool: Callable           # 外部注入的权限决策回调函数
    tool_use_context: dict           # 执行环境（工作目录、取消信号、MCP客户端等）
    max_turns: int                   # Agent循环的最大轮数限制
    deps: dict                       # 外部依赖（如模型客户端、文件系统等，便于测试替换）

# 动态循环状态：每轮结束后更新，保存当前“执行到了哪里”
class LoopState:
    messages: list[Message]          # 截至当前轮的完整消息历史（不断追加）
    turn_count: int                  # 当前执行到了第几轮（从1开始）
    tool_use_context: dict           # 可能被工具修改的上下文（如工作目录变更）
    compact_tracking: dict           # 记录压缩历史，用于避免无限重复压缩
    max_output_tokens_recovery_count: int  # 输出超限后的恢复尝试次数

# 事件联合类型：query()通过yield向外抛出，供UI实时消费
class QueryEvent:
    type: str        # 'text_delta' | 'tool_start' | 'permission_request' | 'tool_result' | 'error'
    data: any        # 具体事件对应的负载


# =====================================================================
# 第二部分：辅助功能函数（上下文压缩、权限判定、MCP连接）
# =====================================================================

# =====================================================================
# 函数: load_mcp_tools()
# 作用: 通过Model Context Protocol连接外部服务器，获取远程工具列表，
#       并将其统一转换为内部Tool对象，合并到本地工具池。
#       这样Agent Loop无需关心工具来自本地还是远端。
# =====================================================================
async def load_mcp_tools(mcp_config):
    # 根据配置（stdio/SSE/HTTP）建立与MCP Server的连接
    mcp_client = await connect_to_mcp_server(mcp_config)  
    # 向服务端请求工具清单（protocol: tools/list）
    remote_tool_schemas = await mcp_client.request_tools_list()  
    converted_tools = []  # 存放转换后的本地Tool对象
    for schema in remote_tool_schemas:
        # 将远程工具的名称、描述和输入Schema映射为内部Tool结构
        # 远程工具的执行逻辑通过RPC回调实现，但接口与本地工具完全一致
        local_tool = convert_remote_schema_to_local_tool(schema, mcp_client)  
        converted_tools.append(local_tool)
    return converted_tools  # 返回可供Agent调用的工具列表

# =====================================================================
# 函数: perform_auto_compact()
# 作用: 在每一轮调用模型之前，检查当前消息历史的Token估算值。
#       如果接近或超出模型的上下文窗口限制，则按优先级执行多层压缩：
#       1. 裁剪超大工具结果（不调模型，直接截断）
#       2. 微压缩早期历史（删除可安全丢弃的旧tool_result）
#       3. 调用模型生成摘要（替换整段旧历史为缩略版）
#       4. 若仍超出，触发熔断机制并报错。
# =====================================================================
async def perform_auto_compact(state: LoopState) -> list[Message]:
    # 估算当前消息总Token数（简单计数或调用tokenizer）
    current_tokens = estimate_tokens(state.messages)  
    # 如果Token数低于阈值（如窗口的80%），直接返回原历史，无需压缩
    if current_tokens < TOKEN_SOFT_LIMIT:
        return state.messages  

    # --- 第一层：工具结果预算（安全丢弃大块输出） ---
    # 遍历消息，找到体积过大的tool_result，用“结果过长，已截断”占位符替换
    for idx, msg in enumerate(state.messages):
        if msg.role == 'tool_result' and len(msg.content) > MAX_TOOL_RESULT_CHARS:
            state.messages[idx].content = "[Tool output truncated due to size]"

    # 重新估算Token，如果已经达标则提前返回
    if estimate_tokens(state.messages) < TOKEN_SOFT_LIMIT:
        return state.messages  

    # --- 第二层：Microcompact（删除较旧且非关键的工具结果） ---
    # 只保留最近N条tool_result，更早的tool_result直接移除（保留消息结构）
    keep_recent = 5  # 保留最近5条工具结果
    tool_result_indices = [i for i, m in enumerate(state.messages) if m.role == 'tool_result']
    if len(tool_result_indices) > keep_recent:
        # 计算需要删除的索引（从最早开始删）
        to_delete = tool_result_indices[:-keep_recent]  
        # 从后往前删除，保证索引不乱
        for idx in reversed(to_delete):
            del state.messages[idx]  

    # 再次估算
    if estimate_tokens(state.messages) < TOKEN_SOFT_LIMIT:
        return state.messages  

    # --- 第三层：会话摘要（调用模型生成历史总结） ---
    # 调用轻量级模型，要求其用简练语言总结当前历史的核心要点
    summary = await generate_conversation_summary(state.messages)  
    # 重建消息列表：摘要作为system消息插入开头 + 保留最近的关键消息（确保连续性）
    new_messages = [
        Message(role="system", content=f"Previous conversation summary: {summary}"),
        *state.messages[-6:]  # 保留最近6条原始消息，维持上下文连贯性
    ]
    # 返回压缩后的新历史；若压缩后仍然超限，则由上层抛出错误终止任务
    return new_messages


# =====================================================================
# 第三部分：模型API调用与响应解析（协议适配层）
# =====================================================================

# =====================================================================
# 函数: call_model_api()
# 作用: 屏蔽Anthropic Messages API、OpenAI Chat Completions、Responses API
#       之间的差异。将内部Message和Tool结构序列化为目标API格式，
#       发起流式请求，并将原始响应块解析为统一的内部事件流（yield）。
#       核心职责：将模型输出的“文本标签”转为结构化的 tool_use 对象。
# =====================================================================
async def call_model_api(
    messages: list[Message],
    system_prompt: str,
    tools: list[Tool],
    deps: dict
):
    # 1. 使用Chat Template将messages和tools渲染为模型训练时使用的格式
    #    例如：<|system|>...<|user|>...<|assistant|>
    #    并将工具描述转为模型能理解的 function calling 格式或XML标签提示
    rendered_prompt = render_chat_template(messages, system_prompt, tools)  

    # 2. 根据deps中配置的provider（如'anthropic'或'openai'）选择对应的API客户端
    api_client = deps.get_api_client()  

    # 3. 发起流式请求，逐块接收原始响应（异步生成器）
    async for raw_chunk in api_client.stream_completion(rendered_prompt):
        # 4. 处理不同类型的原始块
        if raw_chunk.type == 'text_delta':
            # 模型生成的普通文本片段，直接作为事件抛出（UI实现打字机效果）
            yield QueryEvent(type='text_delta', data=raw_chunk.text)  

        elif raw_chunk.type == 'tool_use_start':
            # 模型开始输出工具调用标签（如 <tool_call>），初始化一个缓冲区
            buffer = ""  
            tool_use_id = raw_chunk.id  

        elif raw_chunk.type == 'tool_use_delta':
            # 持续接收工具参数JSON的片段，追加到缓冲区
            buffer += raw_chunk.json_fragment  

        elif raw_chunk.type == 'tool_use_stop':
            # 模型输出了完整的工具调用标签（如 </tool_call>）
            try:
                # 去掉外层标签（如 <tool_call> 和 </tool_call>），只保留内部JSON字符串
                json_text = extract_json_from_tags(buffer)  
                # 解析JSON，得到 { "name": "...", "arguments": {...} }
                parsed = json.loads(json_text)  
                
                # 根据工具名查找对应的本地Tool对象（用于后续校验）
                target_tool = find_tool_by_name(tools, parsed['name'])  
                if target_tool:
                    # 用Tool的input_schema校验参数，防止模型生成非法字段
                    # 若校验失败，会抛出异常，由上层捕获并请求模型重试
                    validated_input = target_tool.input_schema.parse(parsed['arguments'])  
                else:
                    # 如果找不到工具，生成错误事件
                    yield QueryEvent(type='error', data=f"Unknown tool: {parsed['name']}")
                    continue

                # 将解析成功的工具调用封装为标准ToolUseBlock，抛给上层（query_loop）
                yield QueryEvent(
                    type='tool_use_block',
                    data={
                        'id': tool_use_id,
                        'name': parsed['name'],
                        'input': validated_input
                    }
                )
            except (json.JSONDecodeError, SchemaValidationError) as e:
                # 解析或校验失败：生成错误事件，让query_loop决定是否重试
                yield QueryEvent(type='error', data=f"Tool parse error: {str(e)}")


# =====================================================================
# 第四部分：工具执行与权限控制（安全执行层）
# =====================================================================

# =====================================================================
# 函数: execute_tool_with_permissions()
# 作用: 执行单个工具的完整生命周期。严格区分“模型想做什么”和
#       “Harness允许做什么”。先查allow/deny规则，必要时请求用户确认，
#       通过沙箱限制后再执行。执行结果统一转为tool_result消息。
#       拒绝执行不会崩掉整个Agent，而是返回带is_error标志的结果给模型。
# =====================================================================
async def execute_tool_with_permissions(
    tool: Tool,
    validated_input: dict,
    tool_use_id: str,
    state: LoopState,
    can_use_tool_callback: Callable,
    on_progress_callback: Callable
) -> Message:
    # 1. 检查权限（综合项目规则、用户规则、只读分析、沙箱配置）
    permission_result = await tool.check_permissions(validated_input, state.tool_use_context)  

    # 2. 处理deny（明确拒绝）
    if permission_result == 'deny':
        # 构建一个表示“操作被拒绝”的tool_result消息，is_error=True
        return Message(
            role='tool_result',
            tool_use_id=tool_use_id,
            content="Permission denied by system policy.",
            is_error=True
        )

    # 3. 处理ask（需要用户确认）
    if permission_result == 'ask':
        # 向外抛出permission_request事件，等待UI弹出确认框并await用户返回
        user_approved = await on_progress_callback(
            QueryEvent(type='permission_request', data={'tool': tool.name, 'input': validated_input})
        )
        if not user_approved:  # 用户点击了拒绝
            return Message(
                role='tool_result',
                tool_use_id=tool_use_id,
                content="User cancelled the operation.",
                is_error=True
            )

    # 4. 执行工具（此时权限已通过）
    try:
        # 传入取消信号（AbortController），以便用户中途取消任务时能终止执行中的子进程
        # 传入进度回调on_progress，让长任务（如Bash命令）可以持续上报输出
        raw_output = await tool.call(
            validated_input,
            state.tool_use_context,
            on_progress=on_progress_callback
        )
        # 工具正常执行完毕：将内部结构化输出转为模型可读的tool_result block
        result_block = tool.map_result_to_block(raw_output, tool_use_id)
        return Message(
            role='tool_result',
            tool_use_id=tool_use_id,
            content=result_block.content,
            is_error=False
        )
    except Exception as e:
        # 工具执行过程抛出异常（如命令不存在、文件找不到）
        return Message(
            role='tool_result',
            tool_use_id=tool_use_id,
            content=f"Tool execution error: {str(e)}",
            is_error=True
        )


# =====================================================================
# 第五部分：核心Agent循环（大脑中枢）
# =====================================================================

# =====================================================================
# 函数: query_loop()
# 作用: Harness最核心的状态机。维护LoopState，驱动“思考-行动-观察”循环。
#       每一轮迭代包含：预压缩 → 调用模型 → 并行解析工具调用 → 执行工具 → 更新状态。
#       只有模型输出不包含任何tool_use时，循环才会正常结束。
#       它是整个系统的“发动机”。
# =====================================================================
async def query_loop(params: QueryParams, consumed_command_uuids: list[str]) -> AsyncGenerator[QueryEvent, None]:
    # 初始化动态状态：轮次从1开始，消息历史从静态参数中复制
    state = LoopState(
        messages=params.messages.copy(),
        turn_count=1,
        tool_use_context=params.tool_use_context.copy(),
        compact_tracking={},
        max_output_tokens_recovery_count=0
    )

    # ---- 进入主循环（直到模型不再要求执行工具或轮次耗尽） ----
    while True:
        # ----- 步骤A：请求模型前的上下文压缩（防止上下文溢出） -----
        # 如果Token预算接近上限，执行多层压缩（裁剪、微压缩、摘要）
        state.messages = await perform_auto_compact(state)  

        # ----- 步骤B：调用模型并消费流式响应 -----
        # 创建工具执行器，用于管理本轮待执行的工具（支持并行）
        tool_executor = StreamingToolExecutor(
            tools=params.tools,
            can_use_tool=params.can_use_tool,
            context=state.tool_use_context
        )

        # 存放本轮生成的完整助手消息（供持久化和下一轮使用）
        assistant_messages = []  
        # 存放本轮解析出的所有工具调用块（用于判断是否继续循环）
        tool_use_blocks = []  
        # 存放本轮所有工具执行结果（转为UserMessage格式后，追加到历史中）
        tool_result_messages = []  

        # 调用API客户端，逐事件接收模型输出（流式）
        async for event in call_model_api(
            messages=state.messages,
            system_prompt=params.system_prompt,
            tools=params.tools,
            deps=params.deps
        ):
            # 将模型输出事件直接转发给上层UI（实现实时渲染）
            yield event  

            # 如果收到完整的助手消息（assistant role），保存下来
            if event.type == 'assistant_message':
                assistant_messages.append(event.data)  

            # 如果收到工具调用块（tool_use_block），加入队列并交给执行器
            if event.type == 'tool_use_block':
                tool_use_blocks.append(event.data)  # 记录本次有工具调用
                # 将工具调用加入执行器的工作队列（可立即开始准备执行）
                tool_executor.add_task(
                    tool_name=event.data['name'],
                    tool_input=event.data['input'],
                    tool_use_id=event.data['id']
                )

        # ----- 步骤C：判断循环是否应该终止 -----
        # 如果本轮模型没有请求任何工具，说明任务已经完成
        if not tool_use_blocks:
            break  # 退出while循环，结束Agent Loop

        # ----- 步骤D：执行所有待办工具（串行或并行） -----
        # 执行器内部会依次处理权限检查、用户确认、实际调用和结果映射
        async for tool_event in tool_executor.execute_all():
            # 将工具进度/结果事件实时转发给UI
            yield tool_event  
            
		   if tool_event.get("command_uuid"):  
                # ✅ 就是这里！向 query() 传进来的列表追加 UUID
                consumed_command_uuids.append(tool_event["command_uuid"])  
                
            # 如果事件是最终的工具结果（tool_result），收集起来准备追加到历史
            if tool_event.type == 'tool_result':
                # 将tool_result包装为符合API规范的UserMessage（协议要求）
                user_msg = Message(
                    role='user',
                    content=tool_event.data.content,
                    tool_use_id=tool_event.data.tool_use_id
                )
                tool_result_messages.append(user_msg)  

        # ----- 步骤E：更新动态状态，准备下一轮 -----
        # 严格保持协议顺序：原历史 → 本轮的助手消息 → 本轮的tool结果
        state.messages = state.messages + assistant_messages + tool_result_messages
        state.turn_count += 1  # 轮次递增

        # 检查是否达到最大轮数限制（防止死循环）
        if state.turn_count > params.max_turns:
            yield QueryEvent(type='error', data="Max turns exceeded, stopping.")
            break

        # 跳回循环顶部，模型将基于新的tool_result继续决策


# =====================================================================
# 函数: query() [包装器]
# 作用: query_loop()的外层包装。负责记录本次任务使用的命令生命周期，
#       并在循环正常结束后统一标记为“已完成”。它本身也是一个
#       AsyncGenerator，将query_loop产生的所有事件原样透传给上层。
# =====================================================================
async def query(params: QueryParams) -> AsyncGenerator[QueryEvent, None]:
    # 记录本次任务中消费的命令UUID（用于审计和生命周期管理）
    consumed_command_uuids = []  

    # yield from 将内部生成器的所有事件逐个转发给调用方
    # 当query_loop正常return时，会继续执行下面的代码
    async for event in query_loop(params, consumed_command_uuids):
        yield event  # 透传事件

    # 只有query_loop没有抛出异常且正常结束，才标记命令为completed
    for uuid in consumed_command_uuids:
        notify_command_lifecycle(uuid, 'completed')  


# =====================================================================
# 第六部分：Multi-Agent 委派（子任务协调层）
# =====================================================================

# =====================================================================
# 函数: execute_sub_agent()
# 作用: 当父Agent调用AgentTool时触发。创建一个独立的子Query实例，
#       拥有自己的agentId、独立消息历史、隔离的工具权限和取消控制器。
#       支持同步等待（前台）、后台运行、远程轮询等多种模式。
#       核心逻辑：不直接“多开模型”，而是通过任务记录（Task）管理依赖关系。
# =====================================================================
async def execute_sub_agent(parent_state: LoopState, agent_params: dict) -> Message:
    # parent_state：父 Agent 当前的完整动态状态（包含历史消息、工作目录、取消信号等）
    # agent_params：父 Agent 的模型在调用 AgentTool 时传过来的具体参数（比如 {"prompt": "搜索登录模块", "mode": "sync"}）
    
    # 1. 从父上下文中提取子Agent需要的配置（模型、工具、工作目录）
    sub_agent_id = generate_unique_id()  
    sub_tools = parent_state.tool_use_context.get('sub_agent_tools', [])  

    # 2. 构建子任务的静态参数（独立于父任务）
    sub_params = QueryParams(
        messages=[Message(role='user', content=agent_params['prompt'])],
        # 子任务的初始对话只有一条“用户消息”，内容是父模型传过来的 prompt
        system_prompt="You are a sub-agent responsible for code search.",
        tools=sub_tools,
        can_use_tool=parent_state.tool_use_context.get('child_permission_callback'),
        tool_use_context={
            'working_dir': parent_state.tool_use_context['working_dir'],
            'abort_controller': parent_state.tool_use_context['abort_controller'].create_child(),
            'agent_id': sub_agent_id
        },
        max_turns=5
    )

    # 3. 根据模式执行子任务
    if agent_params['mode'] == 'sync':
        # 前台同步：当前父进程阻塞等待子任务结束，直接返回最终结果
        sub_result = await query(sub_params).collect()  
        # .collect() 做了什么：它遍历子 query 产生的所有事件，捕获最后的 __final_state__ 事件，从中提取出最终的状态对象，然后根据你的需求（此处由 execute_sub_agent 的逻辑决定）将其转换为一个简短的文本摘要（f"Sub-agent finished: {sub_result}"
        return Message(
            role='tool_result',
            content=f"Sub-agent finished: {sub_result}",
            tool_use_id=agent_params['parent_tool_use_id']
        )
        # 如果 execute_sub_agent 不返回这个 Message，父 query_loop 的 tool_result_messages 列表就会是空的
        # 关键设计原则（上下文隔离）：父 Agent 只能收到一个“执行结果”的摘要

    elif agent_params['mode'] == 'background':
        # 本地后台：注册为LocalAgentTask，父Agent继续执行，稍后通过TaskGet轮询结果
        task_id = register_background_task(sub_params)  
        return Message(
            role='tool_result',
            content=f"Sub-agent launched in background, task_id={task_id}",
            tool_use_id=agent_params['parent_tool_use_id']
        )

    elif agent_params['mode'] == 'remote':
        # 远程模式：创建RemoteAgentTask会话，通过轮询获取状态
        remote_url = create_remote_session(sub_params)  
        return Message(
            role='tool_result',
            content=f"Remote sub-agent started at {remote_url}",
            tool_use_id=agent_params['parent_tool_use_id']
        ) 


# =====================================================================
# 第七部分：程序入口与REPL交互（用户界面层）
# =====================================================================

# =====================================================================
# 函数: main()
# 作用: 整个Harness的启动入口。负责初始化运行环境（加载项目配置、
#       注册内置工具、连接MCP服务器、设置权限规则），然后进入
#       REPL（读取-求值-输出循环）。它是一次性准备，长时运行。
# =====================================================================
async def main():
    # ---- 1. 初始化环境（此阶段不调用模型） ----
    # 读取当前工作目录（cwd）下的项目配置文件（如CLAUDE.md）
    project_config = load_project_config()  
    # 初始化权限上下文（解析allow/deny规则列表）
    permission_rules = load_permission_rules()  
    # 注册内置基础工具（如BashTool、ReadFileTool、WriteFileTool等）
    builtin_tools = register_builtin_tools()  
    # 连接MCP Server，获取远程工具列表并转换为内部Tool对象
    mcp_tools = await load_mcp_tools(project_config.get('mcp_servers', []))  
    # 合并所有工具，形成最终的全局工具池
    all_tools = builtin_tools + mcp_tools  

    # 组装全局上下文对象（供所有Query复用）
    global_context = {
        'working_dir': os.getcwd(),
        'permission_rules': permission_rules,
        'abort_controller': AbortController(),
        'all_tools': all_tools
    }

    # ---- 2. 进入REPL（长期运行） ----
    while not should_exit():  # 直到用户输入 /exit 或收到终止信号
        # 显示提示符并等待用户输入一行文本（阻塞）
        user_input = await readline()  

        # 处理本地斜杠命令（不进入模型，直接由Harness解析）
        if user_input.startswith('/'):
            # 如 /help, /compact, /exit 等
            await run_slash_command(user_input, global_context)  
            continue  # 处理完命令后，回到循环顶部等待下一次输入

        # ---- 处理普通用户输入（启动一次Agent任务） ----
        # 构建本次任务的静态参数
        params = QueryParams(
            messages=[Message(role='user', content=user_input)],
            system_prompt=build_system_prompt(global_context),  # 注入角色和规则
            tools=global_context['all_tools'],
            can_use_tool=make_permission_callback(global_context['permission_rules']),
            tool_use_context=global_context,
            max_turns=10,
            deps={'api_client': get_model_client()}  # 依赖注入
        )

        # 启动query()生成器，逐事件消费并渲染到终端
        try:
            async for event in query(params):
                # 根据事件类型分别处理
                if event.type == 'text_delta':
                    # 流式输出文本（不换行，实现打字机效果）
                    terminal.write(event.data)  
                elif event.type == 'tool_start':
                    # 显示工具开始执行的状态
                    terminal.print(f"\n[Tool] {event.data['name']} started...")  
                elif event.type == 'permission_request':
                    # 请求用户确认：等待用户输入y/n
                    approved = await terminal.ask_confirmation(event.data)  
                    # 将用户确认结果通过某种方式传回执行器（此处简化）
                elif event.type == 'tool_result':
                    # 显示工具执行结果
                    terminal.print(f"\n[Result] {event.data.content}")  
                elif event.type == 'error':
                    # 显示错误信息
                    terminal.print(f"\n[Error] {event.data}")  
        finally:
            # 无论任务正常结束、被取消还是报错，最后都恢复提示符和光标状态
            restore_prompt()  

        # 打印一个空行，方便区分不同任务
        terminal.print("\n")


# =====================================================================
# 程序执行入口（脚本启动）
# =====================================================================
if __name__ == "__main__":
    # 启动异步主函数，运行整个Harness
    asyncio.run(main())  
```

![deepseek_mermaid_20260812_e4fd41](X:\Downloads\deepseek_mermaid_20260812_e4fd41.png)
