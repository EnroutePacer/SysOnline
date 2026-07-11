import os
import sys
import time
import json
import re
import requests
from collections import deque
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service


class Window_worker:
    Is_new = 1
    Is_ended = 0
    id = ''



def resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)



# 抓取视频函数
def Get_video_links(driver, href_prefix):
    links = []
    # 找到所有 fsresource li
    li_items = driver.find_elements(By.CSS_SELECTOR, "li.activity.fsresource.modtype_fsresource")

    for li in li_items:
        try:
            # 找到 badge-pill
            badge_elem = li.find_elements(By.CSS_SELECTOR, "span.badge-pill")
        except:
            badge_elem = ""
            
        for elem in badge_elem:
            badge_class = elem.get_attribute("class")
            # 如果没有 alert-success，说明未完成
            if "alert-success" not in badge_class:
                try:
                    a_elem = li.find_element(By.CSS_SELECTOR, f'a[href^="{href_prefix}"]')
                    link = a_elem.get_attribute("href")
                    links.append(link)
                except:
                    continue
    return links



# 注入 JS 伪造进度上报（替代原来的真实视频播放）
def Inject_progress_faker(driver):
    """用 AJAX 直接伪造进度上报，无需真实播放视频"""
    status = driver.execute_script("""
        // 如果已经初始化，直接返回当前状态
        if (window.__pfState) {
            return {
                found: true,
                completed: window.__pfState.progress >= 95 || window.__pfState.failCount >= 10,
                progress: window.__pfState.progress,
                running: window.__pfState.running
            };
        }

        // 检查 playerdata 是否可用（由 LMS 页面自动提供）
        if (typeof playerdata === 'undefined' || !playerdata || !playerdata.fsresourceid) {
            return { found: false, completed: false, progress: 0, running: false };
        }

        // 覆盖 alert
        window.alert = function(msg) { console.log("已阻止后台 alert:", msg); };

        // === 参数配置（与 console_script.js 一致） ===
        var CONCURRENT = 50;
        var STEP = 4;
        var BATCH_INTERVAL = 300;
        var MAX_BATCHES = 50;

        // === 初始化状态 ===
        window.__pfState = {
            running: true,
            progress: 0,
            totaltime: 0,
            duration: 0,
            batchCount: 0,
            failCount: 0
        };

        // === 发送单个请求 ===
        function _sendOne(seconds, id) {
            return new Promise(function(resolve) {
                var data = [{
                    'index': 0,
                    'methodname': 'mod_fsresource_set_time',
                    'args': {
                        'fsresourceid': playerdata.fsresourceid,
                        'time': seconds,
                        'finish': 1,
                        'progress': 100,
                        'unique': Date.now() + '_' + Math.random() + '_' + id
                    }
                }];
                $.ajax({
                    url: playerdata.siteUrl + '/lib/ajax/service.php?timestamp=' + new Date().getTime() + '&sesskey=' + playerdata.sesskey,
                    method: 'POST',
                    data: JSON.stringify(data),
                    success: function(resp) {
                        try {
                            var r = typeof resp === 'string' ? JSON.parse(resp) : resp;
                            resolve({
                                id: id,
                                progress: parseFloat(r[0]?.data?.progress) || 0,
                                totaltime: parseInt(r[0]?.data?.totaltime) || 0,
                                duration: parseInt(r[0]?.data?.duration) || 0,
                                success: true
                            });
                        } catch(e) {
                            resolve({ id: id, progress: 0, totaltime: 0, duration: 0, success: false });
                        }
                    },
                    error: function() {
                        resolve({ id: id, progress: 0, totaltime: 0, duration: 0, success: false });
                    }
                });
            });
        }

        // === 发送一批并发请求 ===
        async function _sendBatch(count) {
            var promises = [];
            for (var i = 0; i < count; i++) {
                promises.push(_sendOne(STEP, i + 1));
            }
            var results = await Promise.all(promises);
            return {
                successCount: results.filter(function(r) { return r.success; }).length,
                maxProgress: Math.max.apply(null, results.map(function(r) { return r.progress; }), 0),
                maxTotalTime: Math.max.apply(null, results.map(function(r) { return r.totaltime; }), 0),
                duration: Math.max.apply(null, results.map(function(r) { return r.duration; }), 0),
            };
        }

        // === 异步启动伪造流程 ===
        (async function() {
            try {
                // 第1步：探测当前进度
                var probe = await _sendOne(0, 'probe');
                var inferredDuration = probe.duration || 0;
                var currentProgress = probe.progress;
                var currentTotalTime = probe.totaltime;

                window.__pfState.progress = currentProgress;
                window.__pfState.totaltime = currentTotalTime;
                window.__pfState.duration = inferredDuration;

                // 第2步：批量发送直到100%
                while (currentProgress < 100 && window.__pfState.batchCount < MAX_BATCHES) {
                    window.__pfState.batchCount++;

                    // 计算本次需要发多少请求
                    var needed;
                    if (inferredDuration && inferredDuration > 0) {
                        var remaining = inferredDuration - currentTotalTime;
                        needed = remaining <= 0 ? 1 : Math.min(Math.ceil(remaining / STEP), CONCURRENT);
                    } else {
                        needed = CONCURRENT;
                    }

                    var result = await _sendBatch(needed);

                    if (result.duration > 0 && !inferredDuration) {
                        inferredDuration = result.duration;
                    }

                    if (result.maxProgress > currentProgress) {
                        // 有进展，重置失败计数
                        window.__pfState.failCount = 0;

                        // 如果还不知道总时长，反推
                        if (!inferredDuration) {
                            var gain = result.maxProgress - currentProgress;
                            if (gain > 0) {
                                inferredDuration = Math.round((needed * STEP) / (gain / 100));
                            }
                        }
                        currentProgress = result.maxProgress;
                        currentTotalTime = result.maxTotalTime;
                    } else {
                        // 无进展，累计失败计数
                        window.__pfState.failCount++;
                    }

                    window.__pfState.progress = currentProgress;
                    window.__pfState.totaltime = currentTotalTime;
                    window.__pfState.duration = inferredDuration;

                    // 更新页面显示
                    try {
                        var ps = document.querySelector('.num-bfjd span');
                        if (ps) ps.innerHTML = Math.floor(currentProgress);
                        var ts = document.querySelector('.num-gksc span');
                        if (ts && currentTotalTime) ts.innerHTML = currentTotalTime;
                    } catch(e) {}

                    if (currentProgress >= 100) break;
                    await new Promise(function(r) { setTimeout(r, BATCH_INTERVAL); });
                }

                try {
                    var ps2 = document.querySelector('.num-bfjd span');
                    if (ps2) ps2.innerHTML = '100';
                } catch(e) {}
            } catch(e) {
                console.error('[LMS] 进度伪造异常:', e);
            }
            window.__pfState.running = false;
        })();

        return { found: true, completed: false, progress: 0, running: true };
    """)
    return status



# 阶段性检查函数
def Check_Do_test(driver, video_links, VIDEO_LIST, VIDEO_PREFIX):
    # 操作器回到主窗口
    driver.switch_to.window(Main_window)
    driver.get(VIDEO_LIST)
    is_test_later = driver.find_elements(By.CSS_SELECTOR, "div.availabilityinfo.isrestricted")

    # 找到所有练习元素
    quizzes = driver.find_elements(By.CSS_SELECTOR, "li.activity.quiz.modtype_quiz")
    
    # 存储未完成元素
    quiz_to_finish = []
    for quiz in quizzes:
        is_completed = quiz.find_elements(By.CSS_SELECTOR, "span.badge-pill.alert-success")
        
        if not is_completed:
            try:
            # 获取并存储练习链接
                link_elem = quiz.find_element(By.CSS_SELECTOR, "a.aalink.stretched-link")
            except:
            # 有一个拿不到链接说明后续全未解锁，直接退出循环
                break

            quiz_url = link_elem.get_attribute("href")
            quiz_to_finish.append(quiz_url)
            

    # 逐个处理练习        
    for quiz_url in quiz_to_finish:
        driver.get(quiz_url)
        
        # 尝试进入答题页面
        page_fit = 1
        try:
            time.sleep(1.5)
            # 点击按钮
            start_btn = driver.find_element(By.CSS_SELECTOR, "div.quizstartbuttondiv button[type='submit']")
            driver.execute_script("arguments[0].click();", start_btn)
            time.sleep(1.5)
            confirm_btn = driver.find_element(By.ID, "id_submitbutton")
            driver.execute_script("arguments[0].click();", confirm_btn)
            time.sleep(1.5)
        except:
            page_fit = 0
            pass
        
        # 如果配置了 API 且进入了页面 则开始答题
        if page_fit and APIKEY != '' and MODELNAME != '':
            print("\nTEST_______\n     ! 发现练习\n     ! 发现练习\n     ! 发现练习\n\n......LLM 完成题目中......\n")
            try:
                LLM_kill_test(driver)
            except:
                print("\nLLM 运作失败! ! !\n请手动答题\n")

        # 没有配置或页面错误则手动答题
        else:
            if APIKEY != '' and MODELNAME != '' and not page_fit:
                print("\n由于页面打开失败 LLM 无法工作, 请手动答题, 或更换网络环境后重试\n")
            input("\nTEST_______\n     ! 发现练习\n     ! 发现练习\n     ! 发现练习\n     完成练习后点击回车继续\n")
            input("确定已完成？如确定练习已完成并提交，请再次点击回车\n")
        
        continue
    
    driver.get(VIDEO_LIST)
    driver.refresh()
    new_count = 0
    new_all_links = Get_video_links(driver, VIDEO_PREFIX)
    
    # 更新待处理视频
    for new_link in new_all_links:
        if new_link not in video_links:
            video_links.append(new_link)
            new_count +=1

    if new_count != 0:
        print(f'\n发现{new_count}个新解锁视频\n进程继续中...\n')
    else:
        print('没有检测到新视频，该课程可能已完成\n') 

    if not is_test_later:
        print("未进一步发现练习\n")
    
    # 用内部 is_test_later 来准确修改外部 Is_test
    return is_test_later



# 自动答题请求函数 (vibe)
def LLM_kill_test(driver):
    questions = driver.find_elements(By.CSS_SELECTOR, "div.que")
    if not questions:
        print("\n未找到练习题目。\n")
        return
        
    prompt_text = "你是一个专业助教。请你帮我解答以下的单选/多选题以及判断题。请严格以 JSON 格式返回答案，格式为：{\"answers\": [{\"id\": \"题目ID\", \"choices\": [\"A\"]}]}。\n\n"
    
    question_map = {} # q_id -> { "options": { "A": input_element, ... } }
    
    for q in questions:
        q_id = q.get_attribute("id")
        q_class = q.get_attribute("class")
        
        # 判断题目类型
        if "multichoice" in q_class or "truefalse" in q_class:
            q_type = "多选题" if "multichoiceset" in q_class else "单选题"
            
            try:
                q_text_elem = q.find_element(By.CSS_SELECTOR, "div.qtext")
                prompt_text += f"题目ID: {q_id} ({q_type})\n{q_text_elem.text}\n"
            except:
                pass

            # 寻找选项
            q_options_map = {}
            options_elems = q.find_elements(By.CSS_SELECTOR, "div.answer > div[class^='r']")
            for opt_idx, opt in enumerate(options_elems):
                # 获取选项字母
                try:
                    letter_elem = opt.find_element(By.CSS_SELECTOR, "span.answernumber")
                    letter = letter_elem.text.replace(".", "").strip() 
                # 如果是判断题或没有字母，强制分配 A/B/C/D 
                except:
                    letter = chr(ord('A') + opt_idx) 

                # 选项内容
                try:
                    text_elem = opt.find_element(By.CSS_SELECTOR, "div.flex-fill")
                    opt_text = text_elem.text
                except:
                    opt_text = opt.text
                
                prompt_text += f" {letter}. {opt_text}\n"
                
                # 对应的输入框 (radio or checkbox)
                try:
                    input_elem = opt.find_element(By.CSS_SELECTOR, "input[type='radio'], input[type='checkbox']")
                    q_options_map[letter] = input_elem
                except:
                    pass
                
            prompt_text += "\n"
            question_map[q_id] = {
                "options": q_options_map
            }

    print(f"此练习共 {len(questions)} 道题目，解析文本成功...")
    if not question_map:
        print("有效选择题获取为 0 ，请检查是否在考试页。")
        return

    # 整理请求元素 headers, api_url, payload
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {APIKEY}"
    }

    api_url = BASEURL if BASEURL.endswith("/chat/completions") else BASEURL.rstrip("/") + "/chat/completions"
    
    payload = {
        "model": MODELNAME,
        "messages": [
            {"role": "system", "content": "你是一个严格输出 JSON 的人工智能。绝不能返回任何带有 Markdown 的解释，仅返回合法的 JSON 对象。"},
            {"role": "user", "content": prompt_text}
        ],
        "temperature": 0.1
    }
    
    try:
        # 发送 LLM 请求，读取返回内容
        response = requests.post(api_url, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        resp_json = response.json()
        content = resp_json['choices'][0]['message']['content']
        print(f"{MODELNAME} work! 返回答案成功...")
        
        # 尝试提取 JSON (防止 LLM 添加 markdown 语法块)
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            json_str = match.group(0)
            answers_data = json.loads(json_str)
            answers_list = answers_data.get("answers", [])
            
            for ans in answers_list:
                q_id = ans.get("id")
                choices = ans.get("choices", [])
                if q_id in question_map:
                    for choice in choices:
                        choice = str(choice).strip().upper()
                        input_elem = question_map[q_id]["options"].get(choice)
                        if input_elem:
                            # 判断是否已经被勾选，没勾选的则通过执行 JS 触发 click
                            is_checked = driver.execute_script("return arguments[0].checked;", input_elem)
                            if not is_checked:
                                driver.execute_script("arguments[0].click();", input_elem)
                                time.sleep(0.3)
            # 提交答案
            try:
                time.sleep(0.5)
                end_btn = driver.find_element(By.ID, "mod_quiz-next-nav")
                driver.execute_script("arguments[0].click();", end_btn)
                time.sleep(2.0)
                submit_all_btn = driver.find_element(By.XPATH, "//button[contains(text(), '全部提交并结束')]")
                driver.execute_script("arguments[0].click();", submit_all_btn)
                time.sleep(1.0) 
                confirm_submit_btn = driver.find_element(By.XPATH, "//div[contains(@class, 'confirmation-buttons')]//input[@value='全部提交并结束']")
                driver.execute_script("arguments[0].click();", confirm_submit_btn)

                print("\n......LLM 答题完毕......\n")
                time.sleep(1.5)
            
            except:
                print("\n尝试自动提交答卷时遇错，请自行提交\n")

        else:
            print("未能从大模型返回中解析出 JSON 格式:\n", content)
            
    except Exception as e:
        print(f"大模型请求或自动点击失败: {e}")



    

'''-------------------------------前置部分-------------------------------'''


# 读取配置
config_path = resource_path('config.json')
with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

APIKEY = config.get('APIKEY', '')
BASEURL = config.get('BASEURL', '')
MODELNAME = config.get('MODELNAME', '')


'''-------------------------------驱动部分-------------------------------'''


print("\n等待 “操作提示” 出现后再执行操作，若十秒内没有出现，则是网络问题，关闭重开\n")


# 启动浏览器，跳转登陆界面
options = webdriver.EdgeOptions()
options.add_argument('--log-level=3')
options.add_experimental_option('excludeSwitches', ['enable-automation', 'enable-logging'])

service = Service(executable_path=resource_path("msedgedriver.exe"), log_output=os.devnull)
driver = webdriver.Edge(service=service, options=options)
driver.set_window_size(800, 600)
driver.get('https://lms.sysu.edu.cn/my/')
time.sleep(1.2)

btn_elem = None
LOGIN_POSTFIX = '/login/index.php?authCAS=CAS'
while not btn_elem:
    try:
        btn_elem = driver.find_element(By.CSS_SELECTOR, f'a[href^="{LOGIN_POSTFIX}"]')
    except Exception:
        time.sleep(0.2)
driver.execute_script("arguments[0].click();", btn_elem)



# 登录
input('\n================ 操作提示 ================\n\n1. 登录\n2. 选择一个课程，如《心理健康教育》，点进课程\n3. 请点击回车\n\n================ 操作提示 ================\n\n\n   ')



# 获取源网页和视频
VIDEO_LIST = driver.current_url
VIDEO_ID = 'fsplayer-container-id_html5_api' 
VIDEO_PREFIX = 'https://lms.sysu.edu.cn/mod/fsresource/view.php?id='

Main_window = driver.current_window_handle
video_links = Get_video_links(driver, VIDEO_PREFIX)
video_links = deque(video_links)

print(f'匹配到 {len(video_links)} 个视频链接')
for i, link in enumerate(video_links, 1):
	print(f'{i}. {link}')



# 创建窗口池
x = config.get('sysu_window_count', 1)
windows_pool = []
for k in range(x):
    driver.switch_to.new_window('window')
    driver.set_window_size(500, 600)
    driver.set_window_position(800+40*k, 80*k)

    w = Window_worker()
    w.id = driver.current_window_handle
    w.Is_new = 1
    windows_pool.append(w)
# 初始化窗口工作
On_working = 0
for k in range(x):
    driver.switch_to.window(windows_pool[k].id)
    if len(video_links) == 0:
        break
    driver.get(video_links[0])
    video_links.popleft()
    On_working += 1



# 默认有练习待进入大循环检查
Is_test = 1

# 总工作进度
while len(video_links) > 0 or On_working > 0 or Is_test == 1:

    print(f"\n工作窗口数量: {On_working}")
	# 遍历窗口池
    for k in range(x):
        
        driver.switch_to.window(windows_pool[k].id)
        # 检查是否为空窗口
        if driver.current_url == "about:blank":
            if(len(video_links) > 0):
                driver.get(video_links[0])
                video_links.popleft()
                On_working += 1
            elif not Is_test:
                time.sleep(0.5)
                continue

        else:
            # 检查窗口进度伪造状态
            if windows_pool[k].Is_new:
                # 等待页面加载
                time.sleep(2)
                windows_pool[k].Is_new = 0
            
            # 注入JS，伪造进度上报（替代真实播放）
            try:
                status = Inject_progress_faker(driver)
                time.sleep(0.6)

                if not status.get('found'):
                    print("等待 playerdata 加载...", end='\r')
                    time.sleep(2)
                    continue

                if status.get('completed'):
                    if windows_pool[k].Is_ended == 0:
                        print(f"\n 一个视频刷完！({driver.current_url})\n剩余视频数: {len(video_links)}\n准备切换下一个...")
                    # 有剩余视频则切换
                    if len(video_links) > 0:
                        driver.get(video_links[0])
                        video_links.popleft()
                        windows_pool[k].Is_new = 1

                        # 视频是新补上来的则更新 On_working 数量
                        if windows_pool[k].Is_ended:
                            windows_pool[k].Is_ended = 0
                            On_working +=1
                    # 没有剩余视频则更新 On_working 数量
                    elif len(video_links) == 0 and not windows_pool[k].Is_ended:
                        windows_pool[k].Is_ended = 1
                        On_working -= 1
                        print(f"\n工作窗口数量: {On_working}")
                
            except Exception as e:
                print(f"\n执行 JS 获取数据出错: {e}")
                time.sleep(1)
                

    # 当处理完现有最后一个视频
    if len(video_links) == 0 and On_working == 0:
        # 检查是否有做完练习题 才能看的视频, 并更新待看链接
        Is_test = Check_Do_test(driver, video_links, VIDEO_LIST, VIDEO_PREFIX)
        time.sleep(5.0)
    time.sleep(3.0)



print("\nEND_______\n     自动进程已结束! ! !\n\n     可以检查是否有视频在其他页面\n\n     若没有, 则所有视频已处理完成! ! !\n")
