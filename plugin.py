import asyncio
import json
import aiohttp
import base64
import traceback
import re
import random
import uuid
import time
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Type, Optional, Dict, Any

# 导入新插件系统
from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.base_action import BaseAction
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import ComponentInfo, ActionActivationType, ChatMode
from src.plugin_system.base.config_types import ConfigField
from src.plugin_system import register_plugin

# 导入依赖的系统组件
from src.common.logger import get_logger
import tomlkit

logger = get_logger("pic_action")

def convert_dict_to_database_message(message_dict: dict) -> Optional["DatabaseMessages"]:
    """将字典转换为DatabaseMessages实例"""
    try:
        from src.common.data_models.database_data_model import DatabaseMessages
        import time
        
        # 提取必要的信息，如果不存在则使用默认值
        message_id = message_dict.get("message_id", "")
        timestamp = message_dict.get("time", time.time())
        chat_id = message_dict.get("chat_id", "")
        processed_plain_text = message_dict.get("processed_plain_text", "")
        
        # 用户信息
        user_id = message_dict.get("user_id", "")
        user_nickname = message_dict.get("user_nickname", "")
        user_cardname = message_dict.get("user_cardname", None)
        user_platform = message_dict.get("user_platform", "")
        
        # 群组信息
        chat_info_group_id = message_dict.get("chat_info_group_id", None)
        chat_info_group_name = message_dict.get("chat_info_group_name", None)
        chat_info_group_platform = message_dict.get("chat_info_group_platform", None)
        
        # 聊天信息
        chat_info_stream_id = message_dict.get("chat_info_stream_id", "")
        chat_info_platform = message_dict.get("chat_info_platform", "")
        chat_info_create_time = message_dict.get("chat_info_create_time", timestamp)
        chat_info_last_active_time = message_dict.get("chat_info_last_active_time", timestamp)
        chat_info_user_id = message_dict.get("chat_info_user_id", user_id)
        chat_info_user_nickname = message_dict.get("chat_info_user_nickname", user_nickname)
        chat_info_user_cardname = message_dict.get("chat_info_user_cardname", user_cardname)
        chat_info_user_platform = message_dict.get("chat_info_user_platform", user_platform)
        
        # 创建DatabaseMessages实例
        db_message = DatabaseMessages(
            message_id=message_id,
            time=timestamp,
            chat_id=chat_id,
            processed_plain_text=processed_plain_text,
            user_id=user_id,
            user_nickname=user_nickname,
            user_cardname=user_cardname,
            user_platform=user_platform,
            chat_info_group_id=chat_info_group_id,
            chat_info_group_name=chat_info_group_name,
            chat_info_group_platform=chat_info_group_platform,
            chat_info_stream_id=chat_info_stream_id,
            chat_info_platform=chat_info_platform,
            chat_info_create_time=chat_info_create_time,
            chat_info_last_active_time=chat_info_last_active_time,
            chat_info_user_id=chat_info_user_id,
            chat_info_user_nickname=chat_info_user_nickname,
            chat_info_user_cardname=chat_info_user_cardname,
            chat_info_user_platform=chat_info_user_platform,
        )
        
        return db_message
    except Exception as e:
        logger.error(f"转换消息字典为DatabaseMessages失败: {e}")
        return None

# ===== Action组件 =====

class Custom_Pic_Action(BaseAction):
    """生成一张图片并发送"""

    focus_activation_type = ActionActivationType.LLM_JUDGE
    normal_activation_type = ActionActivationType.KEYWORD
    mode_enable = ChatMode.ALL
    parallel_action = False
    action_name = "draw_picture"
    action_description = "可以根据特定的描述，生成并发送一张图片"
    activation_keywords = ["画", "绘制", "生成图片", "画图", "draw", "paint"]
    llm_judge_prompt = """
判定是否需要使用图片生成动作的条件：
1. 用户明确要求画图、生成图片或创作图像
2. 用户描述了想要看到的画面或场景
3. 对话中提到需要视觉化展示某些概念
4. 用户想要创意图片或艺术作品
"""
    keyword_case_sensitive = False
    
    action_parameters = {
        "description": "图片描述，输入你想要生成并发送的图片的描述，将描述翻译为英文单词组合，并用','分隔，描述中不要出现中文，必填",
        "target_message": "提出绘图请求的对方的发言内容，格式必须为：（用户名:发言内容），若不清楚是回复谁的话可以为None"
    }
    
    action_require = ["当有人要求你生成并发送一张图片时使用"]
    associated_types = ["text", "image"]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_cache = {}
        self._cache_max_size = 10

    async def execute(self) -> Tuple[bool, Optional[str]]:
        """执行图片生成动作"""
        logger.info(f"{self.log_prefix} 执行绘图模型图片生成动作")

        try:
            return await asyncio.wait_for(
                self._process_image_generation(),
                timeout=600.0
            )
        except asyncio.TimeoutError:
            error_message = "图片生成过程总超时（超过10分钟）"
            logger.error(f"{self.log_prefix} {error_message}")
            await self.apis.send_api.send_text(f"哎呀，处理时间太长了，{error_message}")
            return False, error_message
        except Exception as e:
            logger.error(f"{self.log_prefix} 执行过程中发生未知严重错误: {e!r}", exc_info=True)
            await self.send_text("哎呀，插件遇到了一个意料之外的严重问题，请稍后再试。")
            return False, "未知严重错误"

    async def _process_image_generation(self) -> Tuple[bool, Optional[str]]:
        """核心图片生成与处理逻辑"""
        start_time = time.time()
        
        http_base_url = self.get_config("api.base_url")
        http_api_key = self.get_config("api.api_key")

        if not (http_base_url and http_api_key and http_api_key != "ms-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"):
            error_msg = "抱歉，图片生成功能所需的API密钥未配置，无法提供服务。"
            await self.send_text(error_msg)
            logger.error(f"{self.log_prefix} API Key未配置.")
            return False, "API Key未配置"
        
        description = self.action_data.get("description")
        if not description or not description.strip():
            await self.send_text("你需要告诉我想要画什么样的图片哦~ 比如说'画一只可爱的小猫'")
            return False, "图片描述为空"
        
        description = description.strip()[:1000]

        image_size = self.get_config("generation.default_size", "1024x1024")
        default_model = self.get_config("generation.default_model", "lelexiong1025/realmixpony_v1")

        if not self._validate_image_size(image_size):
            logger.warning(f"{self.log_prefix} 无效的图片尺寸: {image_size}，使用默认值 1024x1024")
            image_size = "1024x1024"

        seed_config = self.get_config("generation.default_seed", 42)
        # 修正：将随机种子范围严格限制在API文档规定的 [0, 2^31 - 1] 之内
        actual_seed = random.randint(0, 2**31 - 1) if seed_config == -1 else seed_config

        use_cache = self.get_config("cache.enabled", True)
        if use_cache:
            # 缓存键现在包含种子，以支持随机种子功能
            cache_key = self._get_cache_key(description, default_model, image_size, actual_seed)
            if cache_key in self._request_cache:
                cached_result = self._request_cache[cache_key]
                logger.info(f"{self.log_prefix} 使用缓存的图片结果 (Seed: {actual_seed})")
                await self.send_text("我之前画过类似的图片，用之前的结果~")
                send_success = await self.send_image(cached_result)
                return send_success, "图片已发送(缓存)"


        # 新增：对 steps 和 guidance 参数进行范围验证和修正
        steps_config = self.get_config("generation.default_steps", 30)
        if not 1 <= steps_config <= 100:
            logger.warning(f"配置的采样步数({steps_config})超出有效范围，将修正为边界值。")
            actual_steps = max(1, min(100, steps_config))
        else:
            actual_steps = steps_config

        guidance_config = self.get_config("generation.default_guidance", 3.5)
        if not 1.5 <= guidance_config <= 20.0:
            logger.warning(f"配置的引导系数({guidance_config})超出有效范围[1.5, 20.0]，将修正为边界值。")
            actual_guidance = max(1.5, min(20.0, guidance_config))
        else:
            actual_guidance = guidance_config

        success, result_or_error = await self._make_http_image_request(
            prompt=description,
            model=default_model,
            size=image_size,
            seed=actual_seed,
            guidance=actual_guidance,
            steps=actual_steps,
        )

        if not success:
            # 绘画失败时发送正常消息，不使用引用回复
            await self.send_text(f"哎呀，生成图片时遇到问题：{result_or_error}")
            return False, f"图片生成失败: {result_or_error}"

        image_url = result_or_error
        logger.info(f"{self.log_prefix} 图片URL获取成功: {image_url[:70]}... 下载并编码.")

        encode_success, encode_result = await self._download_and_encode_base64(image_url)

        if not encode_success:
            # 图片处理失败时发送正常消息，不使用引用回复
            await self.send_text(f"获取到图片URL，但在处理图片时失败了：{encode_result}")
            return False, f"图片处理失败(Base64): {encode_result}"

        base64_image_string = encode_result
        
        # 第一步：发送完成消息，使用引用回复
        end_time = time.time()
        duration = round(end_time - start_time)
        success_message = f"队列已完成，耗时{duration}秒"
        
        if self.has_action_message:
            # 检查 action_message 的类型，如果是字典则转换为DatabaseMessages实例
            if isinstance(self.action_message, dict):
                db_message = convert_dict_to_database_message(self.action_message)
                if db_message:
                    completion_success = await self.send_text(success_message, set_reply=True, reply_message=db_message)
                    logger.info(f"{self.log_prefix} 发送完成消息，引用回复原始消息（已转换字典类型）")
                else:
                    completion_success = await self.send_text(success_message)
                    logger.warning(f"{self.log_prefix} 发送完成消息，转换失败，不使用引用回复")
            else:
                completion_success = await self.send_text(success_message, set_reply=True, reply_message=self.action_message)
                logger.info(f"{self.log_prefix} 发送完成消息，引用回复原始消息")
        else:
            completion_success = await self.send_text(success_message)
            logger.info(f"{self.log_prefix} 发送完成消息，无引用回复")
        
        if not completion_success:
            await self.send_text("完成消息发送失败")
            return False, "完成消息发送失败"

        # 等待一小段时间确保消息发送完成
        await asyncio.sleep(0.5)

        # 第二步：发送图片，同样使用引用回复
        if self.has_action_message:
            # 检查 action_message 的类型，如果是字典则转换为DatabaseMessages实例
            if isinstance(self.action_message, dict):
                db_message = convert_dict_to_database_message(self.action_message)
                if db_message:
                    send_success = await self.send_image(base64_image_string, set_reply=True, reply_message=db_message)
                    logger.info(f"{self.log_prefix} 发送图片，引用回复原始消息（已转换字典类型）")
                else:
                    send_success = await self.send_image(base64_image_string)
                    logger.warning(f"{self.log_prefix} 发送图片，转换失败，不使用引用回复")
            else:
                send_success = await self.send_image(base64_image_string, set_reply=True, reply_message=self.action_message)
                logger.info(f"{self.log_prefix} 发送图片，引用回复原始消息")
        else:
            send_success = await self.send_image(base64_image_string)
            logger.info(f"{self.log_prefix} 发送图片，无引用回复")

        if send_success:
            if self.get_config("cache.enabled", True):
                self._request_cache[cache_key] = base64_image_string
                self._cleanup_cache()
            await self._save_to_gallery(description, image_url, base64_image_string)
            
            return True, "图片已成功生成并以引用回复方式发送"
        else:
            await self.send_text("图片已处理为Base64，但发送失败了。")
            return False, "图片发送失败 (Base64)"

    async def _make_http_image_request(self, **kwargs) -> Tuple[bool, str]:
        """(已重构) 混合模式调用API：先尝试同步，失败则回退到异步"""
        base_url = self.get_config("api.base_url", "")
        api_key = self.get_config("api.api_key", "")
        
        headers = {"Authorization": f"Bearer {api_key}"}
        
        user_prompt = kwargs.get('prompt', '').strip()
        additional_prompt = self.get_config("generation.custom_prompt_add", "").strip()

        if user_prompt and additional_prompt:
            full_prompt = f"{user_prompt}, {additional_prompt.lstrip(', ')}"
        elif additional_prompt:
            full_prompt = additional_prompt.lstrip(', ')
        else:
            full_prompt = user_prompt

        # 新增：确保最终提示词长度符合API规范
        if len(full_prompt) >= 2000:
            logger.warning(f"正向提示词拼接后长度超限({len(full_prompt)}), 将自动截断至1999字符。")
            full_prompt = full_prompt[:1999]

        negative_prompt_str = self.get_config("generation.negative_prompt_add", "")
        if len(negative_prompt_str) >= 2000:
            logger.warning(f"负向提示词长度超限({len(negative_prompt_str)}), 将自动截断至1999字符。")
            negative_prompt_str = negative_prompt_str[:1999]

        payload = {
            "model": kwargs.get('model'),
            "prompt": full_prompt,
            "negative_prompt": negative_prompt_str,
            "size": kwargs.get('size'),
            "seed": kwargs.get('seed'),
            "guidance": kwargs.get('guidance'),
            "steps": kwargs.get('steps'),
        }
        payload = {k: v for k, v in payload.items() if v is not None and v != ""}

        logger.info(f"{self.log_prefix} (HTTP) 尝试同步调用...")
        timeout = aiohttp.ClientTimeout(total=20.0)  # 同步调用超时：快速失败回退到异步
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                # 1. 同步模式尝试
                async with session.post(
                    f"{base_url.rstrip('/')}/v1/images/generations",
                    json=payload, headers=headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"{self.log_prefix} (HTTP) 同步调用API响应: {json.dumps(data, ensure_ascii=False)}")
                        # 修正：同步调用的响应可能是 list 本身，或者在 dict 的 'images' 键下
                        # 兼容两种API响应格式：images 或 output_images
                        image_list = data.get("images") or data.get("output_images")
                        image_url = None

                        if isinstance(image_list, list) and image_list:
                            item = image_list[0]  # 获取列表的第一个元素
                            if isinstance(item, dict):
                                image_url = item.get("url")
                            elif isinstance(item, str):
                                image_url = item

                        if image_url:
                            logger.info(f"{self.log_prefix} (HTTP) 同步调用成功！")
                            return True, image_url
                        else:
                            logger.error(f"{self.log_prefix} (HTTP) 同步调用成功但未能解析出URL。API原始响应: {json.dumps(data, ensure_ascii=False)}")
            except (aiohttp.ServerTimeoutError, asyncio.TimeoutError):
                logger.info(f"{self.log_prefix} (HTTP) 同步调用超时，回退到异步模式。")
            except Exception as e:
                logger.warning(f"{self.log_prefix} (HTTP) 同步调用失败 ({e!r})，回退到异步模式。")

        # 2. 异步模式回退
        # 生成本地唯一请求标识，避免任务ID重复风险
        local_request_id = str(uuid.uuid4())[:8]
        logger.info(f"{self.log_prefix} (HTTP-Async) 提交异步任务... [本地ID: {local_request_id}]")
        
        # 异步任务提交重试机制：3次重试，指数退避
        task_id = None
        submit_timeout = aiohttp.ClientTimeout(total=30.0)  # 异步任务提交超时：提交应该很快
        async with aiohttp.ClientSession(timeout=submit_timeout) as session:
            for retry in range(3):
                try:
                    start_headers = {**headers, "X-ModelScope-Async-Mode": "true"}
                    async with session.post(
                        f"{base_url.rstrip('/')}/v1/images/generations",
                        json=payload, headers=start_headers
                    ) as response:
                        response.raise_for_status()
                        data = await response.json()
                        task_id = data.get("task_id")
                        if task_id:
                            break
                        else:
                            logger.warning(f"{self.log_prefix} (HTTP-Async) 任务提交成功但未获取到任务ID")
                            if retry < 2:
                                wait_time = 3 * (2 ** retry)  # 指数退避：3秒, 6秒, 12秒
                                logger.info(f"{self.log_prefix} (HTTP-Async) [重试 {retry+1}/3] {wait_time}秒后重试任务提交")
                                await asyncio.sleep(wait_time)
                except (aiohttp.ServerTimeoutError, aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
                    # 可重试错误
                    if retry < 2:
                        wait_time = 3 * (2 ** retry)  # 指数退避：3秒, 6秒, 12秒
                        logger.warning(f"{self.log_prefix} (HTTP-Async) [重试 {retry+1}/3] 任务提交失败: {e.__class__.__name__}，{wait_time}秒后重试")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"{self.log_prefix} (HTTP-Async) [最终失败] 任务提交经过3次重试后仍然失败: {e!r}")
                        return False, f"异步任务提交失败: {e!r}"
                except aiohttp.ClientResponseError as e:
                    # 根据状态码判断是否可重试
                    if e.status in [429, 500, 502, 503, 504] and retry < 2:
                        wait_time = 3 * (2 ** retry)  # 指数退避：3秒, 6秒, 12秒
                        logger.warning(f"{self.log_prefix} (HTTP-Async) [重试 {retry+1}/3] 任务提交失败: HTTP {e.status}，{wait_time}秒后重试")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"{self.log_prefix} (HTTP-Async) 任务提交失败: HTTP {e.status} (不可重试)")
                        return False, f"异步任务提交失败: HTTP {e.status}"
                except Exception as e:
                    logger.error(f"{self.log_prefix} (HTTP-Async) 任务提交失败: {e!r}")
                    return False, f"异步任务提交失败: {e!r}"
            
        if not task_id:
            return False, "任务提交成功但未获取到任务ID"

        logger.info(f"{self.log_prefix} (HTTP-Async) 任务提交成功, Task ID: {task_id} [本地ID: {local_request_id}]. 开始轮询...")
        poll_endpoint = f"{base_url.rstrip('/')}/v1/tasks/{task_id}"
        poll_headers = {**headers, "X-ModelScope-Task-Type": "image_generation"}
        
        poll_timeout = aiohttp.ClientTimeout(total=60.0)  # 异步轮询超时：单次轮询
        async with aiohttp.ClientSession(timeout=poll_timeout) as session:
            for i in range(30):
                await asyncio.sleep(5)
                try:
                    async with session.get(poll_endpoint, headers=poll_headers) as poll_response:
                        poll_response.raise_for_status()
                        data = await poll_response.json()
                        logger.info(f"{self.log_prefix} (HTTP-Async) 轮询API响应: {json.dumps(data, ensure_ascii=False)}")
                        status = data.get("task_status")
                        if status == "SUCCEED":
                            output_images_list = data.get("output_images")
                            image_url = None
                            if isinstance(output_images_list, list) and output_images_list:
                                item = output_images_list[0]  # 获取列表的第一个元素
                                if isinstance(item, dict):
                                    image_url = item.get("url")
                                elif isinstance(item, str):
                                    image_url = item

                            if image_url:
                                logger.info(f"{self.log_prefix} (HTTP-Async) 异步任务成功！")
                                return True, image_url
                            logger.error(f"{self.log_prefix} (HTTP-Async) 异步任务成功但未能解析出URL。API原始响应: {json.dumps(data, ensure_ascii=False)}")
                            return False, "异步任务成功但未找到图片URL"
                        elif status == "FAILED":
                            error_details = data.get('message', json.dumps(data, ensure_ascii=False))
                            logger.error(f"{self.log_prefix} (HTTP-Async) 异步任务明确失败，API响应: {json.dumps(data, ensure_ascii=False)}")
                            return False, f"异步任务失败: {error_details}"
                except (aiohttp.ServerTimeoutError, asyncio.TimeoutError):
                    logger.warning(f"{self.log_prefix} (HTTP-Async) 第 {i+1}/30 次轮询超时")
                    continue
                except aiohttp.ClientResponseError as e:
                    logger.warning(f"{self.log_prefix} (HTTP-Async) 第 {i+1}/30 次轮询HTTP错误: {e.status}")
                    continue
                except Exception as e:
                    logger.warning(f"{self.log_prefix} (HTTP-Async) 第 {i+1}/30 次轮询失败: {e!r}")
                    continue
        
        return False, "异步任务轮询超时"

    async def _download_and_encode_base64(self, image_url: str) -> Tuple[bool, str]:
        """使用 aiohttp 下载图片并将其编码为Base64字符串"""
        
        # 图片下载重试机制：3次重试，指数退避
        download_timeout = aiohttp.ClientTimeout(total=360.0)  # 图片下载超时：云服务器带宽低，需要更长时间
        async with aiohttp.ClientSession(timeout=download_timeout) as session:
            for retry in range(3):
                try:
                    async with session.get(image_url) as response:
                        response.raise_for_status()
                        content = await response.read()
                        return True, base64.b64encode(content).decode("utf-8")
                except (aiohttp.ServerTimeoutError, aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
                    # 可重试错误
                    if retry < 2:
                        wait_time = 3 * (2 ** retry)  # 指数退避：3秒, 6秒, 12秒
                        logger.warning(f"{self.log_prefix} (Download) [重试 {retry+1}/3] 图片下载失败: {e.__class__.__name__}，{wait_time}秒后重试")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"{self.log_prefix} (Download) [最终失败] 图片下载经过3次重试后仍然失败: {e!r}")
                        return False, f"图片下载失败: {e!r}"
                except aiohttp.ClientResponseError as e:
                    # 根据状态码判断是否可重试
                    if e.status in [429, 500, 502, 503, 504] and retry < 2:
                        wait_time = 3 * (2 ** retry)  # 指数退避：3秒, 6秒, 12秒
                        logger.warning(f"{self.log_prefix} (Download) [重试 {retry+1}/3] 图片下载失败: HTTP {e.status}，{wait_time}秒后重试")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"{self.log_prefix} (Download) 图片下载失败: HTTP {e.status} (不可重试)")
                        return False, f"图片下载失败: HTTP {e.status}"
                except Exception as e:
                    logger.error(f"{self.log_prefix} (Download) 图片下载或编码时发生错误: {e!r}")
                    return False, f"图片下载或编码时发生错误: {e!r}"
        
        return False, "图片下载失败：未知错误"

    async def _save_to_gallery(self, prompt: str, url: str, b64_data: str):
        """新增：将图片保存到本地画廊"""
        try:
            gallery_path = Path(__file__).parent / "gallery"
            gallery_path.mkdir(exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            # 修正：增强文件名清理，移除或替换所有非法字符，包括空格和引号
            sanitized_prompt = re.sub(r'[\\/*?:"<>|\'\s]+', '_', prompt.strip())
            prompt_summary = sanitized_prompt[:30]
            url_hash = base64.urlsafe_b64encode(url.encode()).decode()[:6]
            filename = f"{timestamp}_{prompt_summary}_{url_hash}.png"
            
            image_data = base64.b64decode(b64_data)
            with open(gallery_path / filename, "wb") as f:
                f.write(image_data)
            logger.info(f"{self.log_prefix} 图片已保存到画廊: {filename}")
        except Exception as e:
            logger.error(f"{self.log_prefix} 保存图片到画廊失败: {e!r}")

    def _get_cache_key(self, description: str, model: str, size: str, seed: int) -> str:
        """根据描述、模型、尺寸和种子生成唯一的缓存键"""
        # 添加用户标识避免不同用户间缓存冲突
        try:
            user_id = getattr(self, 'user_id', 'default_user')
        except:
            user_id = 'default_user'
        return f"{user_id}|{description[:100]}|{model}|{size}|{seed}"

    def _cleanup_cache(self):
        """根据配置清理缓存"""
        max_size = self.get_config("cache.max_size", 10)
        
        # 缓存大小验证
        if max_size <= 0 or max_size > 100:
            logger.warning(f"{self.log_prefix} 缓存大小配置无效({max_size})，使用默认值10")
            max_size = 10
        
        if len(self._request_cache) > max_size:
            num_to_remove = len(self._request_cache) - (max_size // 2)
            keys_to_remove = list(self._request_cache.keys())[:num_to_remove]
            for key in keys_to_remove:
                del self._request_cache[key]

    def _validate_image_size(self, image_size: str) -> bool:
        try:
            width, height = map(int, image_size.split("x"))
            
            # 检查API支持范围
            if not (64 <= width <= 2048 and 64 <= height <= 2048):
                logger.warning(f"{self.log_prefix} 图片尺寸{image_size}超出API支持范围(64-2048)，使用默认值1024x1024")
                return False
            
            # 检查是否为常见支持尺寸
            supported_sizes = ["512x512", "1024x1024", "1024x1280", "1280x1024", "1024x1536", "1536x1024"]
            if image_size not in supported_sizes:
                logger.warning(f"{self.log_prefix} 图片尺寸{image_size}可能不被所有模型支持，建议使用: {', '.join(supported_sizes)}")
            
            return True
        except (ValueError, TypeError):
            logger.warning(f"{self.log_prefix} 图片尺寸格式无效({image_size})，使用默认值1024x1024")
            return False


# ===== Command组件 =====

class ModelHelpCommand(BaseCommand):
    """显示模型管理帮助信息"""
    command_name = "model_help"
    command_description = "显示模型管理的帮助信息"
    command_pattern = r"^/model help$"
    command_help = "使用方法: /model help"
    command_examples = ["/model help"]

    async def execute(self) -> Tuple[bool, str, bool]:
        help_text = """
魔搭模型管理帮助:

- /model help - 显示此帮助信息
- /model list - 查看所有可用模型（别名和真实名）
- /model switch [别名] - 切换到指定模型
- /model test [模型名] [提示词] - 测试新模型是否可用
- /model add [别名] [完整模型名] - 添加新模型
- /model remove [别名] - 删除已添加的模型

使用示例:
- /model test AI-ModelScope/stable-diffusion-v1-5
- /model test AI-ModelScope/stable-diffusion-v1-5 cat, cute
- /model add anime AI-ModelScope/stable-diffusion-v1-5
- /model switch anime

注意: 测试模型时如果不提供提示词，将使用默认的 "1girl" 加上配置的正面、负面提示词。
        """
        await self.send_text(help_text.strip())
        return True, "", True

class ModelListCommand(BaseCommand):
    """列出所有可用模型"""
    command_name = "model_list"
    command_description = "查看所有可用模型"
    command_pattern = r"^/model\s+list$"
    command_help = "使用方法: /model list"
    command_examples = ["/model list"]

    async def execute(self) -> Tuple[bool, str, bool]:
        # 获取用户添加的模型
        models_data = self.plugin._load_models_data()
        custom_models = models_data.get("models", {})
        
        # 直接从配置文件读取当前模型，绕过缓存
        current_model = self.plugin._get_current_model_from_file()
        
        # 构建显示信息
        model_info_lines = []
        current_alias = "默认"
        model_found_in_custom = False
        
        # 添加用户自定义的模型
        for alias, real_name in custom_models.items():
            model_info_lines.append(f"【{alias}】\n- {real_name}")
            if real_name == current_model:
                current_alias = alias
                model_found_in_custom = True
        
        # 只有当前模型不在自定义列表中时，才添加默认条目
        if not model_found_in_custom:
            model_info_lines.append(f"【默认】\n- {current_model}")
        
        if not model_info_lines:
            model_info = "无可用模型"
        else:
            model_info = "\n\n".join(model_info_lines)
        
        message = f"可用模型列表:\n{model_info}\n\n当前使用: 【{current_alias}】({current_model})"
        await self.send_text(message)
        return True, "", True

class ModelSwitchCommand(BaseCommand):
    """切换模型"""
    command_name = "model_switch"
    command_description = "切换到指定模型"
    command_pattern = r"^/model\s+switch\s+(?P<alias>\w+)$"
    command_help = "使用方法: /model switch [别名]"
    command_examples = ["/model switch anime"]

    async def execute(self) -> Tuple[bool, str, bool]:
        alias = self.matched_groups.get("alias")
        models_data = self.plugin._load_models_data()
        models = models_data.get("models", {})
        
        if alias not in models:
            available_aliases = ", ".join(models.keys()) if models else "无"
            await self.send_text(f"别名 '{alias}' 不存在。\n可用别名: {available_aliases}")
            return True, "", True
        
        real_model_name = models[alias]
        
        # 尝试更新当前模型
        try:
            self.plugin._update_current_model(real_model_name)
            await self.send_text(f"已切换到模型: 【{alias}】({real_model_name})")
        except Exception as e:
            logger.error(f"切换模型失败: {e}")
            await self.send_text(f"⚠️ 切换模型失败，请检查日志: 【{alias}】({real_model_name})")
        
        return True, "", True

class ModelTestCommand(BaseCommand):
    """测试新模型是否可用"""
    command_name = "model_test"
    command_description = "测试指定模型是否可用"
    command_pattern = r"^/model\s+test\s+(?P<model_name>\S+)(?:\s+(?P<prompt>.+))?$"
    command_help = "使用方法: /model test [模型名] [提示词(可选)]"
    command_examples = [
        "/model test AI-ModelScope/stable-diffusion-v1-5",
        "/model test AI-ModelScope/stable-diffusion-v1-5 cat, cute"
    ]

    async def execute(self) -> Tuple[bool, str, bool]:
        model_name = self.matched_groups.get("model_name")
        user_prompt = (self.matched_groups.get("prompt") or "").strip()
        
        # 如果没有提供提示词，使用默认的
        if not user_prompt:
            test_prompt = "1girl"
        else:
            test_prompt = user_prompt
        
        await self.send_text(f"正在测试模型: {model_name}\n提示词: {test_prompt}")
        
        # 调用测试方法
        success, result_or_error = await self.plugin._test_model_with_prompt(
            model_name, test_prompt
        )
        
        if success:
            await self.send_text("✅ 模型测试成功！生成的图片:")
            await self.send_image(result_or_error)
        else:
            await self.send_text(f"❌ 模型测试失败: {result_or_error}")
            
        return True, "", True

class ModelAddCommand(BaseCommand):
    """添加新模型"""
    command_name = "model_add"
    command_description = "添加新模型到可用列表"
    command_pattern = r"^/model\s+add\s+(?P<alias>\w+)\s+(?P<model_name>.+)$"
    command_help = "使用方法: /model add [别名] [完整模型名]"
    command_examples = ["/model add anime AI-ModelScope/stable-diffusion-v1-5"]

    async def execute(self) -> Tuple[bool, str, bool]:
        alias = self.matched_groups.get("alias")
        model_name = self.matched_groups.get("model_name").strip()
        
        # 先测试模型是否可用
        await self.send_text(f"正在测试模型 {model_name} 是否可用...")
        success, result_or_error = await self.plugin._test_model_with_prompt(model_name, "1girl")
        
        if success:
            # 保存到数据文件
            models_data = self.plugin._load_models_data()
            models_data["models"][alias] = model_name
            
            # 保存模型数据并检查保存是否成功
            save_success = self.plugin._save_models_data(models_data)
            if save_success:
                await self.send_text(f"✅ 模型测试成功！已添加: 【{alias}】({model_name})")
            else:
                await self.send_text(f"⚠️ 模型测试成功但保存失败，请检查日志: 【{alias}】({model_name})")
        else:
            await self.send_text(f"❌ 模型测试失败，无法添加: {result_or_error}")
            
        return True, "", True

class ModelRemoveCommand(BaseCommand):
    """删除模型"""
    command_name = "model_remove"
    command_description = "从可用列表中删除模型"
    command_pattern = r"^/model\s+remove\s+(?P<alias>\w+)$"
    command_help = "使用方法: /model remove [别名]"
    command_examples = ["/model remove anime"]

    async def execute(self) -> Tuple[bool, str, bool]:
        alias = self.matched_groups.get("alias")
        models_data = self.plugin._load_models_data()
        models = models_data.get("models", {})
        
        if alias not in models:
            available_aliases = ", ".join(models.keys()) if models else "无"
            await self.send_text(f"别名 '{alias}' 不存在。\n可用别名: {available_aliases}")
            return True, "", True
        
        removed_model = models.pop(alias)
        
        # 保存模型数据并检查保存是否成功
        save_success = self.plugin._save_models_data(models_data)
        if save_success:
            await self.send_text(f"已删除模型: 【{alias}】({removed_model})")
        else:
            # 如果保存失败，恢复模型数据
            models[alias] = removed_model
            await self.send_text(f"⚠️ 删除模型失败，请检查日志: 【{alias}】({removed_model})")
        
        return True, "", True

# ===== 插件注册 =====
@register_plugin
class CustomPicPlugin(BasePlugin):
    """根据描述使用魔搭（ModelScope）API生成图片的插件"""
    plugin_name: str = "custom_pic_plugin"
    plugin_version = "2.0.0"
    plugin_author = "Ptrel & Roo"
    enable_plugin: bool = True
    dependencies: List[str] = []
    config_file_name: str = "config.toml"
    
    python_dependencies: List[str] = ["aiohttp", "tomlkit"]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 为所有命令类设置插件实例引用
        for _, command_class in self.get_plugin_components():
            if hasattr(command_class, '__name__') and 'Command' in command_class.__name__:
                command_class.plugin = self
    
    config_section_descriptions = {
        "plugin": "插件基础信息",
        "plugin_control": "插件控制",
        "api": "魔搭API配置",
        "generation": "图片生成参数配置",
        "cache": "结果缓存配置"
    }

    config_schema = {
        "plugin": {
            "name": ConfigField(type=str, default="custom_pic_plugin", description="插件名称，无需修改"),
            "config_version": ConfigField(type=str, default="1.2.1", description="配置文件版本号")
        },
        "plugin_control": {
            "enable": ConfigField(
                type=bool, default=True,
                description="True: 启用此插件, False: 禁用此插件"
            )
        },
        "api": {
            "base_url": ConfigField(
                type=str, default="https://api-inference.modelscope.cn",
                description="魔搭API地址，通常无需修改。"
            ),
            "api_key": ConfigField(
                type=str, default="ms-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                description="请直接填入你的魔搭API令牌（ms-开头的字符串），无需添加`Bearer`前缀。",
                required=True
            ),
        },
        "generation": {
            "default_model": ConfigField(
                type=str, default="lelexiong1025/realmixpony_v1",
                description="默认使用的模型ID。"
            ),
            "default_size": ConfigField(
                type=str, default="1024x1024",
                description="默认的图片尺寸。",
                choices=["512x512", "1024x1024", "1024x1280", "1280x1024", "1024x1536", "1536x1024"],
            ),
            "default_guidance": ConfigField(
                type=float, default=3.5,
                description="模型指导强度，影响图片与提示的关联性。API限制范围: [1.5, 20.0]。"
            ),
            "default_seed": ConfigField(
                type=int, default=42,
                description="随机种子，用于复现图片。设置为 -1 可在每次生成时使用随机种子。"
            ),
            "default_steps": ConfigField(
                type=int, default=30,
                description="采样步数，影响图片细节和生成速度。API限制范围: [1, 100]。"
            ),
            "custom_prompt_add": ConfigField(
                type=str,
                default=",Photorealistic, RAW photo in 8k UHD with ultra high detail. The subject is a person with detailed facial features, including natural skin texture, realistic skin pores, and subtle skin imperfections. The eyes are meticulously detailed, with clear irises and pupils, and the authentic human expression is captured with precision. The person is wearing baggy clothing, which is emphasized (baggy clothing:1.2), and the soft fabric folds are clearly visible, adding to the realism. The clothing has vibrant color accents that stand out against the warm, natural palette of the environment. The lighting is gentle and low-contrast, creating a soft, organic feel. The environment around the subject includes organic shapes and natural elements, enhancing the overall warmth and authenticity of the scene. The physically-based rendering ensures sharp focus and realistic environmental reflections in the eyes, capturing the surrounding details with accuracy.\"#8k超高清原始照片，具有超高细节。受试者是一个具有详细面部特征的人，包括自然皮肤纹理、逼真的皮肤毛孔和微妙的皮肤瑕疵。眼睛细致细致，虹膜和瞳孔清晰，准确捕捉到真实的人类表情。该人穿着宽松的衣服，这是强调（宽松的衣服：1.2），柔软的织物褶皱清晰可见，增加了真实感。这些服装具有鲜明的色彩，与温暖、自然的环境色调形成鲜明对比。灯光柔和，对比度低，营造出柔和、有机的感觉。主体周围的环境包括有机形状和自然元素，增强了场景的整体温暖和真实性。基于物理的渲染可确保眼睛中的清晰焦点和逼真的环境反射，准确捕捉周围的细节。",
                description="正面附加提示词。该参数不参与 LLM 模型转换，属于直接发送的参数，使用英文，使用词语和逗号的形式。⚠️警告：请勿设置过长的提示词，用户输入+此配置的总长度不得超过2000字符，超出将被自动截断。"
            ),
            "negative_prompt_add": ConfigField(
                type=str,
                default="(worst quality, low quality, normal quality:1.4), text, signature, watermark, username, artist name, (deformed, distorted, disfigured:1.3), poorly drawn, bad anatomy, wrong anatomy, extra limb, missing limb, floating limbs, disconnected limbs, mutation, mutated, ugly, disgusting, blurry, amputation, (doll, mannequin, figurine:1.3), (drawing, painting, sketch, cartoon, anime, manga:1.5), cel-shaded, render, 3d, cgi, unreal engine, video game, plastic, airbrushed, oversaturated, overexposed, underexposed, (makeup, heavy makeup:1.2), photoshopped, filter, beautify, perfect skin, (studio backdrop, professional lighting setup:1.2)\"# （质量最差、质量低、质量正常：1.4）、文本、签名、水印、用户名、艺术家姓名、（变形、扭曲、毁容：1.3）、绘制不佳、解剖结构不良、解剖结构错误、肢体外、缺失肢体、浮动肢体、断开肢体、突变、突变、丑陋、恶心、模糊、截肢、（玩偶、人体模型、小雕像：1.3），（绘画、素描、卡通、动漫、漫画：1.5）、cel着色、渲染、3d、cgi、虚幻引擎、视频游戏、塑料、喷绘、过饱和、过度曝光、曝光不足、（化妆、浓妆：1.2）、Photoshop、滤镜、美化、完美肌肤，（工作室背景，专业照明设置：1.2）",
                description="负面附加提示词，保持默认即可。⚠️警告：请勿设置过长，总长度不得超过2000字符，超出将被自动截断。"
            ),
        },
        "cache": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用请求缓存。"),
            "max_size": ConfigField(type=int, default=10, description="最大缓存数量。"),
        }
    }

    def _load_models_data(self) -> Dict[str, Any]:
        """加载模型数据"""
        data_file = Path(__file__).parent / "models.json"
        try:
            if data_file.exists():
                with open(data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # 验证数据格式
                    if not isinstance(data, dict) or "models" not in data:
                        logger.warning(f"模型数据格式不正确，将重置为默认格式")
                        return {"models": {}}
                    # 确保 models 字段是字典类型
                    if not isinstance(data["models"], dict):
                        logger.warning(f"models 字段格式不正确，将重置为空字典")
                        data["models"] = {}
                    logger.info(f"成功加载模型数据，共 {len(data['models'])} 个模型")
                    return data
            else:
                logger.info("models.json 文件不存在，将创建新的模型数据文件")
                # 创建默认数据并保存
                default_data = {"models": {}}
                self._save_models_data(default_data)
                return default_data
        except json.JSONDecodeError as e:
            logger.error(f"解析 models.json 失败: {e}")
            # 如果文件存在但解析失败，备份损坏的文件
            if data_file.exists():
                backup_file = data_file.with_suffix(".json.backup")
                try:
                    data_file.rename(backup_file)
                    logger.info(f"已将损坏的 models.json 备份为 {backup_file}")
                except Exception as backup_error:
                    logger.error(f"备份损坏的 models.json 失败: {backup_error}")
            return {"models": {}}
        except Exception as e:
            logger.error(f"加载模型数据时发生未知错误: {e}")
            return {"models": {}}

    def _save_models_data(self, data: Dict[str, Any]):
        """保存模型数据"""
        data_file = Path(__file__).parent / "models.json"
        
        # 验证数据格式
        if not isinstance(data, dict) or "models" not in data:
            logger.error(f"数据格式不正确，无法保存模型数据")
            return False
        
        # 确保 models 字段是字典类型
        if not isinstance(data["models"], dict):
            logger.error(f"models 字段格式不正确，无法保存模型数据")
            return False
        
        try:
            # 确保目录存在
            data_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 创建临时文件，写入成功后再重命名，避免写入过程中出错导致文件损坏
            temp_file = data_file.with_suffix(".json.tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            
            # 重命名临时文件为正式文件
            temp_file.replace(data_file)
            
            logger.info(f"成功保存模型数据，共 {len(data['models'])} 个模型")
            return True
        except PermissionError as e:
            logger.error(f"保存模型数据失败，没有文件写入权限: {e}")
            return False
        except OSError as e:
            logger.error(f"保存模型数据失败，文件系统错误: {e}")
            return False
        except Exception as e:
            logger.error(f"保存模型数据时发生未知错误: {e}")
            return False

    def _update_current_model(self, model_name: str):
        """更新当前使用的模型到 config.toml"""
        try:
            config_path = Path(__file__).parent / "config.toml"
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = tomlkit.load(f)
                config_data["generation"]["default_model"] = model_name
            
            with open(config_path, 'w', encoding='utf-8') as f:
                tomlkit.dump(config_data, f)
            
            logger.info(f"已更新配置文件中的模型为: {model_name}")
        except Exception as e:
            logger.error(f"更新配置文件失败: {e}")

    def _get_current_model_from_file(self) -> str:
        """直接从配置文件读取当前模型，绕过缓存"""
        try:
            config_path = Path(__file__).parent / "config.toml"
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = tomlkit.load(f)
                return config_data["generation"]["default_model"]
        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")
            return self.get_config("generation.default_model", "lelexiong1025/realmixpony_v1")

    async def _test_model_with_prompt(self, model_name: str, user_prompt: str) -> Tuple[bool, str]:
        """使用完整的混合模式逻辑测试模型"""
        try:
            # 构建完整的配置字典
            plugin_config_dict = {
                "api": {
                    "base_url": self.get_config("api.base_url"),
                    "api_key": self.get_config("api.api_key")
                },
                "generation": {
                    "custom_prompt_add": self.get_config("generation.custom_prompt_add"),
                    "negative_prompt_add": self.get_config("generation.negative_prompt_add"),
                    "default_size": self.get_config("generation.default_size"),
                    "default_guidance": self.get_config("generation.default_guidance"),
                    "default_steps": self.get_config("generation.default_steps")
                },
                "cache": {
                    "enabled": False  # 测试时不使用缓存
                }
            }
            
            # 创建最小化的 chat_stream 对象
            class MockChatStream:
                def __init__(self):
                    self.stream_id = "test_stream"
                    self.platform = "test"
            
            # 创建临时 Action 实例，使用完整配置
            temp_action = Custom_Pic_Action(
                action_data={},
                reasoning="",
                cycle_timers={},
                thinking_id="test",
                chat_stream=MockChatStream(),
                plugin_config=plugin_config_dict
            )
            
            # 使用与正常绘画完全相同的参数调用 API（包括同步→异步回退）
            success, result_or_error = await temp_action._make_http_image_request(
                prompt=user_prompt,
                model=model_name,
                size=self.get_config("generation.default_size", "1024x1024"),
                seed=42,  # 测试用固定种子
                guidance=self.get_config("generation.default_guidance", 3.5),
                steps=self.get_config("generation.default_steps", 30),
            )
            
            if success:
                # 下载并编码图片
                image_url = result_or_error
                encode_success, base64_image = await temp_action._download_and_encode_base64(image_url)
                if encode_success:
                    return True, base64_image
                else:
                    return False, f"图片下载失败: {base64_image}"
            else:
                return False, result_or_error
                
        except Exception as e:
            return False, f"测试过程中出错: {str(e)}"

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""
        if not self.get_config("plugin_control.enable", True):
            logger.info(f"插件 {self.plugin_name} 已被禁用，跳过加载。")
            return []
        
        components = [(Custom_Pic_Action.get_action_info(), Custom_Pic_Action)]
        
        # 添加命令组件
        components.extend([
            (ModelHelpCommand.get_command_info(), ModelHelpCommand),
            (ModelListCommand.get_command_info(), ModelListCommand),
            (ModelSwitchCommand.get_command_info(), ModelSwitchCommand),
            (ModelTestCommand.get_command_info(), ModelTestCommand),
            (ModelAddCommand.get_command_info(), ModelAddCommand),
            (ModelRemoveCommand.get_command_info(), ModelRemoveCommand),
        ])
        
        return components

# ===== 插件注册 =====
