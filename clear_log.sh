#!/bin/bash

# 清理日志脚本
# 清理 data/logs 目录下的所有日志文件
# 用于开发环境项目启动的时候清理历史日志。

LOG_DIR="data/logs"

# 检查日志目录是否存在
if [ ! -d "$LOG_DIR" ]; then
    echo "错误: 日志目录 $LOG_DIR 不存在"
    exit 1
fi

# 清理所有 .log 文件
echo "正在清理日志文件..."
find "$LOG_DIR" -type f -name "*.log" -exec rm -f {} \;

# 清理 task 子目录下的所有文件
if [ -d "$LOG_DIR/task" ]; then
    echo "正在清理 task 子目录..."
    find "$LOG_DIR/task" -type f -exec rm -f {} \;
fi

echo "日志清理完成！"
