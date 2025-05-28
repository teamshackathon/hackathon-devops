import streamlit as st
import requests
import kubernetes as k8s
import time
import threading
import json
import os
import ctypes
from datetime import datetime
import streamlit_authenticator as stauth

import yaml
from yaml.loader import SafeLoader

st.set_page_config(
    page_title="Git Release Monitor & K8s Manager",
    page_icon="🚀",
    layout="wide"
)

with open('config.yaml') as file:
    config = yaml.load(file, Loader=SafeLoader)

# Pre-hashing all plain text passwords once
stauth.Hasher.hash_passwords(config['credentials'])

authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

# 認証処理
authenticator.login()


if st.session_state["authentication_status"] is None:
    st.warning('ユーザー名とパスワードを入力してください')
elif not st.session_state["authentication_status"]:
    st.error('ユーザー名/パスワードが間違っています')
elif st.session_state["authentication_status"]:

    # セッション状態の初期化
    if 'monitoring_threads' not in st.session_state:
        st.session_state.monitoring_threads = {}
    if 'is_monitoring' not in st.session_state:
        st.session_state.is_monitoring = {}
    if 'logs' not in st.session_state:
        st.session_state.logs = []
    if 'latest_releases' not in st.session_state:
        st.session_state.latest_releases = {}
    if 'release_histories' not in st.session_state:
        st.session_state.release_histories = {}
    if 'config' not in st.session_state:
        st.session_state.config = {
            'targets': [
                {
                    'id': 'target1',
                    'name': 'Default Target',
                    'github_repo': '',
                    'github_token': '',
                    'k8s_namespace': 'default',
                    'k8s_deployment': '',
                    'polling_interval': 60,
                    'is_active': False,
                    'latest_release': None,  # Add latest_release field to store the last detected release
                }
            ]
        }
    if 'selected_target_index' not in st.session_state:
        st.session_state.selected_target_index = 0
    if 'next_target_id' not in st.session_state:
        st.session_state.next_target_id = 2  # target1はデフォルトで使用済み

    # ログ追加関数
    def add_log(message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state.logs.append(f"[{timestamp}] {message}")
        if len(st.session_state.logs) > 100:  # 最大100件までログを保持
            st.session_state.logs.pop(0)

    # Githubリリース取得関数
    def get_github_releases(repo, token=None):
        headers = {}
        if token:
            headers["Authorization"] = f"token {token}"
        
        url = f"https://api.github.com/repos/{repo}/releases"
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return []

    # Kubernetes設定ロード関数
    def load_k8s_config():
        try:
            # kubeconfig からの設定ロード試行
            k8s.config.load_kube_config()
            add_log("Loaded Kubernetes config from kubeconfig file")
        except Exception:
            try:
                # クラスター内実行時の設定ロード試行
                k8s.config.load_incluster_config()
                add_log("Loaded in-cluster Kubernetes config")
            except Exception as e:
                add_log(f"Failed to load Kubernetes config: {e}")
                return False
        return True

    # Kubernetesデプロイメントのリスタート関数
    def restart_k8s_deployment(namespace, deployment_name):
        try:
            apps_v1 = k8s.client.AppsV1Api()
            now = datetime.utcnow().isoformat()
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": now
                            }
                        }
                    }
                }
            }

            # 実行
            apps_v1.patch_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body=patch
            )
            # add_log(f"Successfully restarted deployment {deployment_name} in namespace {namespace}")
            return True
        except Exception:
            # add_log(f"Error restarting deployment: {e}")
            return False

    # Kubernetesデプロイメントのステータス取得関数
    def get_deployment_status(namespace, deployment_name):
        try:
            if not load_k8s_config():
                return None
                
            apps_v1 = k8s.client.AppsV1Api()
            core_v1 = k8s.client.CoreV1Api()
            
            # デプロイメント情報の取得
            deployment = apps_v1.read_namespaced_deployment(
                name=deployment_name,
                namespace=namespace
            )
            
            # ポッド一覧取得
            pods = core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=",".join([f"{k}={v}" for k, v in deployment.spec.selector.match_labels.items()])
            )
            
            # コンテナイメージ情報の取得
            containers = deployment.spec.template.spec.containers
            images = [{"name": container.name, "image": container.image} for container in containers]
            
            # デプロイメント詳細情報
            status_info = {
                "name": deployment.metadata.name,
                "namespace": deployment.metadata.namespace,
                "created_at": deployment.metadata.creation_timestamp,
                "replicas": {
                    "desired": deployment.spec.replicas,
                    "current": deployment.status.replicas if deployment.status.replicas else 0,
                    "ready": deployment.status.ready_replicas if deployment.status.ready_replicas else 0,
                    "available": deployment.status.available_replicas if deployment.status.available_replicas else 0,
                    "unavailable": deployment.status.unavailable_replicas if deployment.status.unavailable_replicas else 0
                },
                "images": images,
                "strategy": deployment.spec.strategy.type,
                "updated_at": deployment.status.conditions[-1].last_update_time if deployment.status.conditions else None,
                "pods": []
            }
            
            # ポッド詳細情報の取得
            for pod in pods.items:
                pod_containers = []
                for container in pod.status.container_statuses if pod.status.container_statuses else []:
                    container_info = {
                        "name": container.name,
                        "ready": container.ready,
                        "restarts": container.restart_count,
                        "image": container.image,
                        "image_id": container.image_id
                    }
                    pod_containers.append(container_info)
                
                pod_info = {
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "ip": pod.status.pod_ip,
                    "node": pod.spec.node_name,
                    "start_time": pod.status.start_time,
                    "containers": pod_containers
                }
                status_info["pods"].append(pod_info)
            
            return status_info
            
        except Exception as e:
            add_log(f"Error getting deployment status: {e}")
            return None

    # モニタリングスレッド関数
    def monitoring_thread(target_id, target_name, repo, token, namespace, deployment, interval, monitoring_state):
        # add_log(f"[{target_name}] Starting monitoring for {repo}, checking every {interval} seconds")
        
        # 設定からlast_release_tagを取得する（存在する場合）
        last_release_tag = None
        # スレッド間で共有状態から初期値を設定
        if f"{target_id}_stored_release_tag" in monitoring_state:
            last_release_tag = monitoring_state[f"{target_id}_stored_release_tag"]
            print(last_release_tag)
        while target_id in monitoring_state and monitoring_state[target_id]:
            try:
                releases = get_github_releases(repo, token)
                if releases:
                    latest_release = releases[0]
                    print(last_release_tag, latest_release['tag_name'])
                    
                    # スレッド間の共有変数で最新のリリース情報を共有
                    monitoring_state[f"{target_id}_latest_release"] = latest_release
                    monitoring_state[f"{target_id}_releases"] = releases
                    
                    # 前回チェック時から新しいリリースが出たら再起動
                    if last_release_tag is None:
                        # 初回実行時
                        print(f"[{target_name}] Initial release detected: {latest_release['tag_name']}")
                        monitoring_state[f"{target_id}_new_release"] = True
                    elif latest_release['tag_name'] != last_release_tag:
                        # 新しいリリースが検出された
                        print(f"[{target_name}] New release detected: {latest_release['tag_name']}")
                        
                        # Kubernetesデプロイメントを再起動
                        restart_result = restart_k8s_deployment(namespace, deployment)
                        if restart_result:
                            print(f"[{target_name}] Automatically restarted deployment for new release {latest_release['tag_name']}")
                            last_release_tag = latest_release['tag_name']
                            monitoring_state[f"{target_id}_stored_release_tag"] = last_release_tag
                        else:
                            print(f"[{target_name}] Failed to restart deployment for new release {latest_release['tag_name']}")
                        
                        monitoring_state[f"{target_id}_new_release"] = True
                    else:
                        # 変更なし
                        print(f"[{target_name}] No new releases detected")
                    
                    # 最新のリリースタグを記録
                    last_release_tag = latest_release['tag_name']
                    print(last_release_tag, latest_release['tag_name'])
                    monitoring_state[f"{target_id}_stored_release_tag"] = last_release_tag
                else:
                    print(f"[{target_name}] No releases found or error getting releases")
            except Exception as e:
                print(f"[{target_name}] Error in monitoring thread: {e}")
            
            # 指定された間隔で待機
            time.sleep(interval)

    # セッションとスレッド間の共有状態を管理するディクショナリ（スレッドセーフ）
    shared_monitoring_state = {}

    # モニタリング開始関数
    def start_monitoring(target_index):
        target = st.session_state.config['targets'][target_index]
        target_id = target['id']

        if target_id in st.session_state.is_monitoring and st.session_state.is_monitoring[target_id]:
            add_log(f"[{target['name']}] Monitoring is already running")
            return
        
        if not target['github_repo'] or not target['k8s_deployment']:
            st.error(f"GitHub repository and Kubernetes deployment must be set for {target['name']}")
            return
        
        # スレッド間で共有するモニタリング状態を初期化
        st.session_state.is_monitoring[target_id] = True
        shared_monitoring_state[target_id] = True
        
        # 設定にアクティブフラグを更新
        st.session_state.config['targets'][target_index]['is_active'] = True
        
        # 前回のリリースタグがあれば共有状態に設定
        if target['latest_release'] and 'tag_name' in target['latest_release']:
            shared_monitoring_state[f"{target_id}_stored_release_tag"] = target['latest_release']['tag_name']
        
        # 変更をconfig.jsonに保存
        save_config()

        add_log(f"[{target['name']}] Starting monitoring for {target['github_repo']}, checking every {target['polling_interval']} seconds")

        # すでに同じIDのスレッドが存在する場合はスキップ
        if target_id in st.session_state.monitoring_threads:
            add_log(f"[{target['name']}] Monitoring thread already exists")
            return
        
        thread = threading.Thread(
            target=monitoring_thread,
            args=(
                target_id,
                target['name'],
                target['github_repo'],
                target['github_token'],
                target['k8s_namespace'],
                target['k8s_deployment'],
                target['polling_interval'],
                shared_monitoring_state
            )
        )
        thread.daemon = True
        thread.start()
        st.session_state.monitoring_threads[target_id] = thread

    # モニタリング停止関数
    def stop_monitoring(target_index):
        target = st.session_state.config['targets'][target_index]
        target_id = target['id']
        
        if target_id not in st.session_state.is_monitoring or not st.session_state.is_monitoring[target_id]:
            add_log(f"[{target['name']}] Monitoring is not running")
            return
        
        # Streamlitのセッションステートと共有変数の両方を更新
        st.session_state.is_monitoring[target_id] = False
        if target_id in shared_monitoring_state:
            shared_monitoring_state[target_id] = False
            
        st.session_state.config['targets'][target_index]['is_active'] = False

        # 変更をconfig.jsonに保存
        save_config()

        add_log(f"[{target['name']}] Stopping monitoring")
        # スレッドを強制終了する
        if target_id in st.session_state.monitoring_threads:
            thread = st.session_state.monitoring_threads[target_id]
            
            try:
                # スレッドIDを取得
                thread_id = thread.ident
                
                # スレッドへSystemExitを送信
                if thread_id:
                    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                        ctypes.c_long(thread_id), 
                        ctypes.py_object(SystemExit)
                    )
                    if res == 0:
                        add_log(f"[{target['name']}] Invalid thread ID, could not terminate thread")
                    elif res != 1:
                        # 複数のスレッドを終了させてしまった場合、リセット
                        ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, None)
                        add_log(f"[{target['name']}] Failed to terminate thread cleanly")
                
                # スレッドが終了するのを少し待つ
                time.sleep(0.1)
            except Exception as e:
                add_log(f"[{target['name']}] Error terminating thread: {e}")
            finally:
                # 監視スレッド辞書から削除
                del st.session_state.monitoring_threads[target_id]
                add_log(f"[{target['name']}] Removed monitoring thread")

    # 設定保存関数
    def save_config():
        try:
            config_dir = os.environ.get('CONFIG_PATH', '')
            config_path = os.path.join(config_dir, 'config.json') if config_dir else 'config.json'
            # os.makedirs(os.path.dirname(config_dir), exist_ok=True)
            with open(config_path, 'w') as f:
                json.dump(st.session_state.config, f, indent=2)
            add_log("Configuration saved successfully")
        except Exception as e:
            add_log(f"Error saving configuration: {e}")

    # 設定読込関数
    def load_config():
        try:
            config_dir = os.environ.get('CONFIG_PATH', '')
            config_path = os.path.join(config_dir, 'config.json') if config_dir else 'config.json'
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    st.session_state.config = json.load(f)
                
                # ターゲットごとの最新リリース情報をsession_stateにロード
                for target in st.session_state.config['targets']:
                    if 'latest_release' in target and target['latest_release']:
                        target_id = target['id']
                        st.session_state.latest_releases[target_id] = target['latest_release']
                        
                        # アクティブなモニタリングがある場合、共有状態にもセット
                        if target['is_active']:
                            shared_monitoring_state[f"{target_id}_stored_release_tag"] = target['latest_release']['tag_name']
                
                add_log("Configuration loaded successfully")
        except Exception as e:
            add_log(f"Error loading configuration: {e}")

    # 初回起動時に設定を読み込む
    if 'config_loaded' not in st.session_state:
        load_config()
        
        # アクティブなモニタリングを再開
        for i, target in enumerate(st.session_state.config['targets']):
            if target['is_active']:
                add_log(f"Auto-restarting monitoring for {target['name']}")
                start_monitoring(i)
        
        st.session_state.config_loaded = True

    # ロールバック実行関数
    def rollback_to_version(target_index, tag_name):
        target = st.session_state.config['targets'][target_index]
        if not target['k8s_deployment'] or not target['k8s_namespace']:
            st.error(f"Kubernetes deployment and namespace must be set for {target['name']}")
            return
        
        # 実際のアプリケーションではこの部分をカスタマイズして
        # 特定のバージョンにロールバックするロジックを実装する
        add_log(f"[{target['name']}] Rolling back to version {tag_name}")
        
        if load_k8s_config():
            # ここでは簡単にロールアウトを再起動するだけ
            # 実際のアプリケーションでは特定バージョンのデプロイが必要

            api_v1 = k8s.client.AppsV1Api()
            deployment = api_v1.read_namespaced_deployment(target['k8s_deployment'], target['k8s_namespace'])

            for container in deployment.spec.template.spec.containers:
                # コンテナイメージを指定されたタグに変更
                container.image = f"{container.image.split(':')[0]}:{tag_name}"

            # デプロイメントを更新
            api_v1.patch_namespaced_deployment(
                name=target['k8s_deployment'],
                namespace=target['k8s_namespace'],
                body=deployment
            )

            add_log(f"[{target['name']}] Successfully rolled back to {tag_name}")


    # Streamlit UI
    st.title("🚀 Git Release Monitor & K8s Manager")

    # 新しいターゲット追加関数
    def add_target():
        new_target = {
            'id': f'target{st.session_state.next_target_id}',
            'name': f'Target {st.session_state.next_target_id}',
            'github_repo': '',
            'github_token': '',
            'k8s_namespace': 'default',
            'k8s_deployment': '',
            'polling_interval': 60,
            'is_active': False,
            'latest_release': None  # Add latest_release field
        }
        st.session_state.config['targets'].append(new_target)
        st.session_state.selected_target_index = len(st.session_state.config['targets']) - 1
        st.session_state.next_target_id += 1
        add_log(f"Added new monitoring target: {new_target['name']}")

    # ターゲット削除関数
    def delete_target(index):
        if index == 0 and len(st.session_state.config['targets']) == 1:
            st.error("Cannot delete the last target")
            return
            
        target = st.session_state.config['targets'][index]
        target_id = target['id']
        
        # モニタリングが実行中なら停止
        if target_id in st.session_state.is_monitoring and st.session_state.is_monitoring[target_id]:
            stop_monitoring(index)
        
        # セッションデータから削除
        if target_id in st.session_state.monitoring_threads:
            del st.session_state.monitoring_threads[target_id]
        if target_id in st.session_state.is_monitoring:
            del st.session_state.is_monitoring[target_id]
        if target_id in st.session_state.latest_releases:
            del st.session_state.latest_releases[target_id]
        if target_id in st.session_state.release_histories:
            del st.session_state.release_histories[target_id]
        
        # 共有状態からも削除
        keys_to_remove = [key for key in shared_monitoring_state if key.startswith(f"{target_id}_")]
        for key in keys_to_remove:
            del shared_monitoring_state[key]
        
        # 設定から削除
        removed_target = st.session_state.config['targets'].pop(index)
        add_log(f"Removed monitoring target: {removed_target['name']}")
        
        # 設定を保存
        save_config()
        
        # 選択中のインデックスを調整
        if st.session_state.selected_target_index >= len(st.session_state.config['targets']):
            st.session_state.selected_target_index = len(st.session_state.config['targets']) - 1

    # サイドバー（設定）
    with st.sidebar:
        st.header("⚙️ Target Management")
        st.text("Welcome " + st.session_state['name'] + "!")
        authenticator.logout("Logout")
        
        # ターゲット選択
        target_names = [t['name'] for t in st.session_state.config['targets']]
        selected_target = st.selectbox(
            "Select Target", 
            options=range(len(target_names)),
            format_func=lambda i: f"{target_names[i]} {'🟢' if (st.session_state.config['targets'][i]['id'] in st.session_state.is_monitoring and st.session_state.is_monitoring[st.session_state.config['targets'][i]['id']]) else '🔴'}",
            key="target_selector",
            on_change=lambda: setattr(st.session_state, 'selected_target_index', st.session_state.target_selector)
        )
        
        st.session_state.selected_target_index = selected_target
        current_target = st.session_state.config['targets'][selected_target]
        
        # ターゲット操作ボタン
        col1, col2 = st.columns(2)
        with col1:
            st.button("Add New Target", on_click=add_target, type="secondary")
        with col2:
            st.button("Delete Target", on_click=lambda: delete_target(st.session_state.selected_target_index), 
                    type="secondary", disabled=len(st.session_state.config['targets']) <= 1 and selected_target == 0)
        
        st.markdown("---")
        
        # 選択したターゲットの設定
        st.subheader(f"Settings for {current_target['name']}")
        
        # ターゲット名
        target_name = st.text_input(
            "Target Name", 
            value=current_target['name'],
            key=f"target_name_{selected_target}"
        )
        st.session_state.config['targets'][selected_target]['name'] = target_name
        
        # GitHub 設定
        st.text_input(
            "GitHub Repository (owner/repo)", 
            value=current_target['github_repo'],
            key=f"github_repo_input_{selected_target}",
            on_change=lambda: st.session_state.config['targets'][selected_target].update(
                {'github_repo': st.session_state[f"github_repo_input_{selected_target}"]}
            )
        )
        
        # st.text_input(
        #     "GitHub Token (Optional)", 
        #     value=current_target['github_token'],
        #     key=f"github_token_input_{selected_target}",
        #     type="password",
        #     on_change=lambda: st.session_state.config['targets'][selected_target].update(
        #         {'github_token': st.session_state[f"github_token_input_{selected_target}"]}
        #     )
        # )
        
        # Kubernetes 設定
        st.text_input(
            "K8s Namespace", 
            value=current_target['k8s_namespace'],
            key=f"k8s_namespace_input_{selected_target}",
            on_change=lambda: st.session_state.config['targets'][selected_target].update(
                {'k8s_namespace': st.session_state[f"k8s_namespace_input_{selected_target}"]}
            )
        )
        
        st.text_input(
            "K8s Deployment", 
            value=current_target['k8s_deployment'],
            key=f"k8s_deployment_input_{selected_target}",
            on_change=lambda: st.session_state.config['targets'][selected_target].update(
                {'k8s_deployment': st.session_state[f"k8s_deployment_input_{selected_target}"]}
            )
        )
        
        st.number_input(
            "Polling Interval (seconds)",
            min_value=10,
            value=current_target['polling_interval'],
            key=f"polling_interval_input_{selected_target}",
            on_change=lambda: st.session_state.config['targets'][selected_target].update(
                {'polling_interval': st.session_state[f"polling_interval_input_{selected_target}"]}
            )
        )
        
        # 設定保存/読み込みボタン
        col1, col2 = st.columns(2)
        with col1:
            st.button("Save Config", on_click=save_config)
        with col2:
            st.button("Load Config", on_click=load_config)
        
        st.markdown("---")
        
        # モニタリング状態表示
        st.subheader("Monitoring Status")
        if current_target['id'] in st.session_state.is_monitoring and st.session_state.is_monitoring[current_target['id']]:
            st.success("Monitoring is active")
        else:
            st.warning("Monitoring is inactive")

        # モニタリング開始/停止ボタン

        target_id = current_target['id']
        if target_id in st.session_state.is_monitoring and st.session_state.is_monitoring[target_id]:
            st.button("Stop Monitoring", on_click=lambda: stop_monitoring(selected_target), type="primary")
        else:
            st.button("Start Monitoring", on_click=lambda: start_monitoring(selected_target), type="primary")

    # メインコンテンツ
    tab1, tab2, tab3, tab4 = st.tabs(["Release Monitor", "Release History", "K8s Status", "Logs"])

    # タブ1: リリースモニター
    with tab1:
        selected_target = st.session_state.selected_target_index
        current_target = st.session_state.config['targets'][selected_target]
        target_id = current_target['id']
        
        st.subheader(f"Monitor Status: {current_target['name']}")
        
        # 現在の監視状態を表示
        is_active = target_id in st.session_state.is_monitoring and st.session_state.is_monitoring[target_id]
        status = "🟢 Active" if is_active else "🔴 Inactive"
        st.info(f"Monitoring Status: {status}")
        
        # スレッドからの新しいリリース情報を確認して更新
        if target_id in shared_monitoring_state and f"{target_id}_new_release" in shared_monitoring_state and shared_monitoring_state[f"{target_id}_new_release"]:
            st.session_state.latest_releases[target_id] = shared_monitoring_state[f"{target_id}_latest_release"]
            st.session_state.release_histories[target_id] = shared_monitoring_state[f"{target_id}_releases"]
            
            # configにも最新リリース情報を保存
            st.session_state.config['targets'][selected_target]['latest_release'] = shared_monitoring_state[f"{target_id}_latest_release"]
            save_config()  # 設定をファイルに保存
            
            shared_monitoring_state[f"{target_id}_new_release"] = False
        
        # 最新リリース情報表示
        st.subheader("Latest Release")
        
        # 手動でリリースを確認するボタン
        if st.button("Check Releases Now"):
            if not current_target['github_repo']:
                st.error(f"GitHub repository must be set for {current_target['name']}")
            else:
                releases = get_github_releases(
                    current_target['github_repo'],
                    current_target['github_token']
                )
                if releases:
                    st.session_state.latest_releases[target_id] = releases[0]
                    st.session_state.release_histories[target_id] = releases
                    
                    # configにも最新リリース情報を保存
                    st.session_state.config['targets'][selected_target]['latest_release'] = releases[0]
                    save_config()  # 設定をファイルに保存
                    
                    add_log(f"Successfully fetched releases for {current_target['github_repo']}")
                    # st.experimental_rerun()
                else:
                    st.error("Failed to fetch releases or no releases available")
        
        # リリース情報の表示（configからの取得も試みる）
        latest = None
        if target_id in st.session_state.latest_releases and st.session_state.latest_releases[target_id]:
            latest = st.session_state.latest_releases[target_id]
        elif current_target['latest_release']:
            latest = current_target['latest_release']
            
        if latest:
            st.markdown(f"""
            ### {latest['name'] or latest['tag_name']}
            **Tag:** {latest['tag_name']}  
            **Published At:** {latest['published_at']}  
            **Description:** {latest['body'][:500] + '...' if len(latest['body']) > 500 else latest['body']}
            """)
            
            if latest.get('assets'):
                st.write("Assets:")
                for asset in latest['assets']:
                    st.write(f"- [{asset['name']}]({asset['browser_download_url']})")
        else:
            st.write("No release information available")

    # タブ2: リリース履歴
    with tab2:
        selected_target = st.session_state.selected_target_index
        current_target = st.session_state.config['targets'][selected_target]
        target_id = current_target['id']
        
        st.subheader(f"Release History: {current_target['name']}")
        
        releases = None
        # セッション状態から履歴を取得
        if target_id in st.session_state.release_histories and st.session_state.release_histories[target_id]:
            releases = st.session_state.release_histories[target_id]
        
        if releases:
            # リリース履歴テーブル
            release_data = []
            for release in releases:
                release_data.append({
                    "Tag": release['tag_name'],
                    "Name": release['name'] or release['tag_name'],
                    "Published At": release['published_at'],
                    "Actions": release['tag_name']  # ここにアクションボタン用のタグ名を入れる
                })
            
            # テーブル表示
            df_releases = st.dataframe(
                release_data,
                column_config={
                    "Tag": st.column_config.TextColumn("Tag"),
                    "Name": st.column_config.TextColumn("Name"),
                    "Published At": st.column_config.DatetimeColumn("Published At"),
                    "Actions": st.column_config.TextColumn("Actions", width="small")
                },
                hide_index=True
            )
            
            # ロールバック対象の選択
            selected_version = st.selectbox(
                "Select version to rollback:",
                options=["latest"] + [release["tag_name"] for release in releases],
                format_func=lambda x: f"{x} ({next((r['name'] for r in releases if r['tag_name'] == x), x)})"
            )
            
            if st.button("Execute Rollback"):
                rollback_to_version(selected_target, selected_version)
        else:
            # リリース履歴取得ボタン
            if st.button("Fetch Release History"):
                if not current_target['github_repo']:
                    st.error(f"GitHub repository must be set for {current_target['name']}")
                else:
                    releases = get_github_releases(
                        current_target['github_repo'],
                        current_target['github_token']
                    )
                    if releases:
                        st.session_state.release_histories[target_id] = releases
                        
                        # 最新リリースも更新
                        st.session_state.latest_releases[target_id] = releases[0]
                        st.session_state.config['targets'][selected_target]['latest_release'] = releases[0]
                        save_config()
                        
                        add_log(f"Successfully fetched release history for {current_target['github_repo']}")
                    else:
                        st.error("Failed to fetch releases or no releases available")
            else:
                st.write("No release history available")

    # タブ3: Kubernetesステータス
    with tab3:
        selected_target = st.session_state.selected_target_index
        current_target = st.session_state.config['targets'][selected_target]
        target_id = current_target['id']
        
        st.subheader(f"Kubernetes Status: {current_target['name']}")
        
        if not current_target['k8s_namespace'] or not current_target['k8s_deployment']:
            st.warning("Kubernetes namespace and deployment must be set to view status")
        else:
            # ステータス取得ボタン
            status_col1, status_col2 = st.columns([3, 1])
            with status_col2:
                refresh_status = st.button("Refresh Status")
            
            with status_col1:
                st.write(f"**Deployment:** {current_target['k8s_deployment']} in namespace {current_target['k8s_namespace']}")
            
            # ステータス情報の取得と表示
            if refresh_status or 'k8s_status' not in st.session_state:
                st.session_state.k8s_status = {}
            
            status = get_deployment_status(
                current_target['k8s_namespace'], 
                current_target['k8s_deployment']
            )
            
            if status:
                st.session_state.k8s_status[target_id] = status
                
                # デプロイメント概要情報
                st.subheader("Deployment Overview")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Desired Replicas", status["replicas"]["desired"])
                with col2:
                    st.metric("Available Replicas", status["replicas"]["available"])
                with col3:
                    st.metric("Ready Replicas", status["replicas"]["ready"])
                
                # ストラテジーとタイムスタンプ情報
                st.write(f"**Strategy:** {status['strategy']}")
                st.write(f"**Created:** {status['created_at']}")
                if status['updated_at']:
                    st.write(f"**Last Updated:** {status['updated_at']}")
                
                # イメージ情報
                st.subheader("Container Images")
                for image in status["images"]:
                    image_name = image["image"]
                    # イメージタグ（バージョン）の抽出
                    image_tag = image_name.split(":")[-1] if ":" in image_name else "latest"
                    st.info(f"**{image['name']}**: `{image_name}` (Tag: **{image_tag}**)")
                
                # ポッド情報
                st.subheader("Pods")
                if status["pods"]:
                    # ポッドの概要情報をテーブルで表示
                    pod_data = []
                    for pod in status["pods"]:
                        pod_status = "✅ Running" if pod["phase"] == "Running" else f"⚠️ {pod['phase']}"
                        
                        # コンテナの状態を集約
                        containers_ready = all(c["ready"] for c in pod["containers"])
                        container_status = "✅ Ready" if containers_ready else "⚠️ Not Ready"
                        
                        # リスタート回数を計算
                        total_restarts = sum(c["restarts"] for c in pod["containers"])
                        
                        pod_data.append({
                            "Pod Name": pod["name"],
                            "Status": pod_status,
                            "Containers": container_status,
                            "Restarts": total_restarts,
                            "IP": pod["ip"] or "N/A",
                            "Node": pod["node"] or "N/A",
                            "Start Time": pod["start_time"]
                        })
                    
                    # ポッド情報テーブル
                    st.dataframe(
                        pod_data,
                        column_config={
                            "Pod Name": st.column_config.TextColumn("Pod Name"),
                            "Status": st.column_config.TextColumn("Status"),
                            "Containers": st.column_config.TextColumn("Containers"),
                            "Restarts": st.column_config.NumberColumn("Restarts"),
                            "IP": st.column_config.TextColumn("IP"),
                            "Node": st.column_config.TextColumn("Node"),
                            "Start Time": st.column_config.DatetimeColumn("Start Time")
                        },
                        hide_index=True
                    )
                    
                    # ポッド詳細情報（展開可能）
                    for i, pod in enumerate(status["pods"]):
                        with st.expander(f"Pod Details: {pod['name']}"):
                            st.write(f"**Phase:** {pod['phase']}")
                            st.write(f"**IP:** {pod['ip'] or 'N/A'}")
                            st.write(f"**Node:** {pod['node'] or 'N/A'}")
                            st.write(f"**Start Time:** {pod['start_time']}")
                            
                            st.subheader("Containers")
                            for container in pod["containers"]:
                                container_status = "✅ Ready" if container["ready"] else "⚠️ Not Ready"
                                st.markdown(f"""
                                **{container['name']}**: {container_status}
                                - **Image:** `{container['image']}`
                                - **Image ID:** `{container['image_id']}`
                                - **Restarts:** {container['restarts']}
                                """)
                else:
                    st.warning("No pods found for this deployment")
            else:
                st.error("Failed to get deployment status. Make sure your Kubernetes configuration is correct and the deployment exists.")
                
                # 最後に取得したステータスがある場合は表示
                if target_id in st.session_state.k8s_status:
                    st.info("Showing last known status:")
                    st.json(st.session_state.k8s_status[target_id])
                else:
                    st.warning("No status information available")

    # タブ4: ログ
    with tab4:
        st.subheader("Logs")
        log_container = st.container()
            
        if st.button("Clear Logs"):
            st.session_state.logs = []
            # 最新のログを表示
        with log_container:
            logs_text = "\n".join(st.session_state.logs)
            st.text_area("Application Logs", logs_text, height=400)
