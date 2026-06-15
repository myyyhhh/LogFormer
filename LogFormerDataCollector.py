import subprocess
import json
import time
import os
import signal
import sys
from datetime import datetime

# 配置
NAMESPACE = "online-boutique"
OUTPUT_DIR = "log_data/json_logs"
EXCLUDE_PODS = ["loadgenerator", "redis"]  # 排除的Pod

class LogCollector:
    def __init__(self, namespace=NAMESPACE, output_dir=OUTPUT_DIR):
        self.namespace = namespace
        self.output_dir = output_dir
        self.processes = {}
        self.running = False
        
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)
        
        # 注册信号处理（优雅退出）
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """处理Ctrl+C信号"""
        print(f"\n\n[{datetime.now().strftime('%H:%M:%S')}] Received signal {signum}, stopping...")
        self.stop()
        sys.exit(0)
    
    def get_all_pods(self):
        """获取所有Pod名称"""
        cmd = [
            "kubectl", "get", "pods", "-n", self.namespace,
            "-o", "jsonpath={.items[*].metadata.name}"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error getting pods: {result.stderr}")
            return []
        
        pods = result.stdout.strip().split()
        
        # 过滤掉不需要的Pod
        filtered_pods = [
            pod for pod in pods 
            if not any(exclude in pod for exclude in EXCLUDE_PODS)
        ]
        
        return filtered_pods
    
    def extract_service_name(self, pod_name):
        """从Pod名称提取服务名"""
        # 例如: frontend-759775d795-kwd9l -> frontend
        parts = pod_name.split('-')
        # 去掉最后的hash部分（通常是2段）
        if len(parts) > 2:
            return '-'.join(parts[:-2])
        return parts[0]
    
    def start_collecting(self, duration_seconds=None, phase_name="normal", service_filter=None):
        """
        开始收集日志
        
        Args:
            duration_seconds: 收集时长（秒），None表示持续收集直到手动停止
            phase_name: 阶段名称（normal/fault），用于文件名
            service_filter: 服务名过滤（如 "frontend"），None表示收集所有服务
        """
        pods = self.get_all_pods()

        if not pods:
            print("No pods found!")
            return

        # 如果指定了服务名，过滤出匹配的Pod
        if service_filter:
            pods = [
                pod for pod in pods
                if pod.startswith(service_filter + '-')
            ]
            if not pods:
                print(f"Error: No pods found for service '{service_filter}'")
                print("Available services:")
                for pod in self.get_all_pods():
                    print(f"  - {self.extract_service_name(pod)}")
                return

        print("=" * 80)
        print(f"LOG COLLECTION - {phase_name.upper()} PHASE")
        print("=" * 80)
        print(f"Namespace: {self.namespace}")
        print(f"Output directory: {self.output_dir}")
        print(f"Found {len(pods)} pods:")
        for pod in pods:
            service = self.extract_service_name(pod)
            print(f"  - {pod}")
        print("=" * 80)
        
        if duration_seconds:
            print(f"Duration: {duration_seconds} seconds ({duration_seconds/60:.1f} minutes)")
        else:
            print("Mode: Continuous (Press Ctrl+C to stop)")
        
        print(f"\nStarting collection at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-" * 80)
        
        self.running = True
        start_time = time.time()
        
        # 为每个Pod启动日志收集进程
        for pod in pods:
            service = self.extract_service_name(pod)
            
            # 文件名格式: {service}_{phase}.json
            if phase_name:
                output_file = os.path.join(self.output_dir, f"{service}_{phase_name}.json")
            else:
                output_file = os.path.join(self.output_dir, f"{service}.json")
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting: {pod} -> {output_file}")
            
            # kubectl logs 命令
            cmd = [
                "kubectl", "logs", "-n", self.namespace,
                pod, "-f", "--timestamps"
            ]
            
            # 启动进程
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=open(output_file, 'w', encoding='utf-8'),
                    stderr=subprocess.DEVNULL
                )
                self.processes[pod] = {
                    'proc': proc,
                    'service': service,
                    'file': output_file
                }
            except Exception as e:
                print(f"Error starting collection for {pod}: {e}")
        
        print("-" * 80)
        print(f"All collectors started. Waiting...\n")
        
        # 等待指定时长或手动停止
        try:
            if duration_seconds:
                remaining = duration_seconds
                while remaining > 0 and self.running:
                    time.sleep(min(10, remaining))
                    elapsed = time.time() - start_time
                    remaining = duration_seconds - elapsed
                    progress = elapsed / duration_seconds * 100
                    print(f"\rProgress: [{int(progress):3d}%] {elapsed/60:.1f}/{duration_seconds/60:.1f} min", 
                          end='', flush=True)
                print()  # 换行
            else:
                # 持续收集，直到手动停止
                while self.running:
                    time.sleep(1)
        except KeyboardInterrupt:
            print("\nInterrupted by user")
        
        # 停止收集
        self.stop()
        
        # 统计结果
        self.print_summary(start_time, phase_name)
    
    def stop(self):
        """停止所有日志收集进程"""
        if not self.running:
            return
        
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Stopping all collectors...")
        self.running = False
        
        for pod, info in self.processes.items():
            try:
                info['proc'].terminate()
                info['proc'].wait(timeout=5)
            except:
                info['proc'].kill()
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] All collectors stopped.")
    
    def print_summary(self, start_time, phase_name):
        """打印收集统计"""
        elapsed = time.time() - start_time
        
        print("\n" + "=" * 80)
        print("COLLECTION SUMMARY")
        print("=" * 80)
        print(f"Phase: {phase_name}")
        print(f"Duration: {elapsed:.0f} seconds ({elapsed/60:.1f} minutes)")
        print(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-" * 80)
        
        total_lines = 0
        total_size = 0
        
        stats = []
        for pod, info in self.processes.items():
            output_file = info['file']
            service = info['service']
            
            if os.path.exists(output_file):
                # 统计行数
                with open(output_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = sum(1 for line in f if line.strip())
                
                size = os.path.getsize(output_file)
                total_lines += lines
                total_size += size
                
                stats.append((service, lines, size))
        
        # 按服务名排序
        stats.sort(key=lambda x: x[0])
        
        print(f"\n{'Service':<25} {'Lines':>8} {'Size':>10}")
        print("-" * 45)
        
        for service, lines, size in stats:
            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024 * 1024:
                size_str = f"{size/1024:.1f} KB"
            else:
                size_str = f"{size/1024/1024:.1f} MB"
            
            print(f"{service:<25} {lines:>8} {size_str:>10}")
        
        print("-" * 45)
        
        if total_size < 1024:
            total_size_str = f"{total_size} B"
        elif total_size < 1024 * 1024:
            total_size_str = f"{total_size/1024:.1f} KB"
        else:
            total_size_str = f"{total_size/1024/1024:.1f} MB"
        
        print(f"{'TOTAL':<25} {total_lines:>8} {total_size_str:>10}")
        print("=" * 80)
        
        # 保存时间戳
        timestamp_file = os.path.join(self.output_dir, f"timestamps_{phase_name}.json")
        timestamps = {
            'phase': phase_name,
            'start': datetime.fromtimestamp(start_time).isoformat(),
            'end': datetime.now().isoformat(),
            'duration_seconds': elapsed,
            'total_lines': total_lines,
            'total_size_bytes': total_size
        }
        
        with open(timestamp_file, 'w') as f:
            json.dump(timestamps, f, indent=2)
        
        print(f"\n✓ Timestamps saved to: {timestamp_file}")
        
        if total_lines < 1000:
            print(f"\n⚠️  Warning: Only {total_lines} logs collected.")
            print(f"   Consider collecting for a longer duration.")
        else:
            print(f"\n✓ Success! Collected {total_lines} log entries.")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='OnlineBoutique Log Collector')
    parser.add_argument('--duration', type=int, default=None,
                       help='Collection duration in seconds (None for continuous)')
    parser.add_argument('--phase', type=str, default='normal',
                       help='Phase name: normal or fault')
    parser.add_argument('--namespace', type=str, default=NAMESPACE,
                       help='Kubernetes namespace')
    parser.add_argument('--output-dir', type=str, default=OUTPUT_DIR,
                       help='Output directory')
    parser.add_argument('--service', type=str, default=None,
                       help='Collect logs for a specific service only (e.g. frontend). Empty = all services.')

    args = parser.parse_args()

    collector = LogCollector(
        namespace=args.namespace,
        output_dir=args.output_dir
    )

    collector.start_collecting(
        duration_seconds=args.duration,
        phase_name=args.phase,
        service_filter=args.service
    )


if __name__ == "__main__":
    main()


    # python LogFormerDataCollector.py --duration 300 --phase normal


    # 只收集 frontend 的正常日志

# python LogFormerDataCollector.py --duration 300 --phase normal --service frontend