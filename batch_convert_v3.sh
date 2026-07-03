#!/bin/bash

# 批量 PSD 转 HTML 转换脚本（改进版 - 保存每个项目的输出）

INPUT_DIR="/Users/zzz/Downloads/input"
OUTPUT_BASE_DIR="$HOME/psd2code_batch_output"
TEMP_OUTPUT="$HOME/psd2code_output"

mkdir -p "$OUTPUT_BASE_DIR"

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║          批量 PSD 转 HTML 转换 (v3 - 保存每个项目)           ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "📁 输入目录: $INPUT_DIR"
echo "📁 输出目录: $OUTPUT_BASE_DIR"
echo ""

# 统计变量
SUCCESS=0
FAILED=0
TOTAL=0
DECLARE -a SUCCESS_FILES
DECLARE -a FAILED_FILES

# 遍历所有 PSD 文件
for psd_file in "$INPUT_DIR"/*.psd; do
    if [ -f "$psd_file" ]; then
        TOTAL=$((TOTAL + 1))
        FILE_NAME=$(basename "$psd_file")
        FILE_STEM="${FILE_NAME%.psd}"
        PROJECT_OUTPUT="$OUTPUT_BASE_DIR/$FILE_STEM"
        
        echo ""
        echo "[$TOTAL/12] 正在转换: $FILE_NAME"
        echo "─────────────────────────────────────────────────────────────────"
        
        # 执行转换
        cd /Users/zzz/psd2code
        
        python3 psd_to_code.py "$psd_file" > "$OUTPUT_BASE_DIR/${FILE_STEM}_convert.log" 2>&1
        CONVERT_STATUS=$?
        
        if [ $CONVERT_STATUS -eq 0 ]; then
            # 检查输出是否存在
            if [ -d "$TEMP_OUTPUT" ]; then
                # 移动输出到项目目录
                mkdir -p "$PROJECT_OUTPUT"
                
                # 复制所有文件
                cp -r "$TEMP_OUTPUT"/* "$PROJECT_OUTPUT/" 2>/dev/null
                
                # 统计 HTML 文件
                HTML_COUNT=$(find "$PROJECT_OUTPUT" -name "*.html" 2>/dev/null | wc -l)
                CSS_COUNT=$(find "$PROJECT_OUTPUT" -name "*.css" 2>/dev/null | wc -l)
                IMG_COUNT=$(find "$PROJECT_OUTPUT" \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" -o -name "*.webp" \) 2>/dev/null | wc -l)
                
                if [ $HTML_COUNT -gt 0 ]; then
                    echo "✅ 转换成功: $FILE_NAME"
                    echo "   HTML: $HTML_COUNT  CSS: $CSS_COUNT  图片: $IMG_COUNT"
                    echo "   输出: $PROJECT_OUTPUT"
                    SUCCESS=$((SUCCESS + 1))
                    SUCCESS_FILES+=("$FILE_NAME ($HTML_COUNT HTML)")
                else
                    echo "⚠️  转换完成但未生成 HTML: $FILE_NAME"
                    FAILED=$((FAILED + 1))
                    FAILED_FILES+=("$FILE_NAME (无 HTML)")
                fi
            else
                echo "⚠️  输出目录不存在: $FILE_NAME"
                FAILED=$((FAILED + 1))
                FAILED_FILES+=("$FILE_NAME (无输出)")
            fi
        else
            echo "❌ 转换失败: $FILE_NAME"
            echo "   错误代码: $CONVERT_STATUS"
            FAILED=$((FAILED + 1))
            FAILED_FILES+=("$FILE_NAME (错误: $CONVERT_STATUS)")
        fi
    fi
done

# 清理临时输出
rm -rf "$TEMP_OUTPUT" 2>/dev/null

echo ""
echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    转换完成总结                              ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "📊 转换统计:"
echo "   总计: $TOTAL 个文件"
echo "   ✅ 成功: $SUCCESS 个"
echo "   ❌ 失败: $FAILED 个"
echo "   成功率: $(( SUCCESS * 100 / TOTAL ))%"
echo ""

if [ $SUCCESS -gt 0 ]; then
    echo "✅ 成功转换的项目:"
    for item in "${SUCCESS_FILES[@]}"; do
        echo "   • $item"
    done
    echo ""
fi

if [ $FAILED -gt 0 ]; then
    echo "❌ 失败或跳过的项目:"
    for item in "${FAILED_FILES[@]}"; do
        echo "   • $item"
    done
    echo ""
fi

echo "📁 输出目录: $OUTPUT_BASE_DIR"
echo ""
echo "📋 日志文件:"
ls -1 "$OUTPUT_BASE_DIR"/*_convert.log 2>/dev/null | xargs -I {} basename {} | head -3
echo "   ... 等 $TOTAL 个日志文件"
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo ""

# 最后统计
echo "📈 最终统计:"
echo ""
echo "总大小:"
du -sh "$OUTPUT_BASE_DIR" 2>/dev/null
echo ""
echo "项目数量:"
ls -d "$OUTPUT_BASE_DIR"/*/ 2>/dev/null | wc -l | xargs echo "  项目数:"
echo ""
echo "总 HTML 文件数:"
find "$OUTPUT_BASE_DIR" -name "*.html" 2>/dev/null | wc -l | xargs echo "  "
echo ""
echo "总 CSS 文件数:"
find "$OUTPUT_BASE_DIR" -name "*.css" 2>/dev/null | wc -l | xargs echo "  "
echo ""
echo "总图片文件数:"
find "$OUTPUT_BASE_DIR" \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" -o -name "*.webp" \) 2>/dev/null | wc -l | xargs echo "  "
echo ""
