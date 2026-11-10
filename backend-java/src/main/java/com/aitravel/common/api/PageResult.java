package com.aitravel.common.api;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * 分页响应信封。
 * <p>
 * 05 §5 的统一约定：列表接口必须分页，{@code size ≤ 50}——
 * 上限在 Controller 层夹紧，避免前端传 size=100000 把库拖垮。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class PageResult<T> {

    private List<T> records;
    private long total;
    private long page;
    private long size;

    public static <T> PageResult<T> of(List<T> records, long total, long page, long size) {
        return new PageResult<>(records, total, page, size);
    }
}
