"""
불량률 계산 Tool - 전체 및 그룹(범주형)별 불량률을 계산하고 카테고리 간 유의성 검정을 수행합니다.
"""

def compute_failure_rate(
        data: object = None,
        target_column: str = None,
        positive_label: int = 1,
        group_column: str = None,
        min_samples: int = 30,
        output_dir: str = "artifacts/metrics",
) ->  dict[str, object]:
    """
    전체 및 그룹(범주형)별 불량률을 계산합니다.
    group_column를 지정하면 카테고리별 불량률과 카이제곱 유의성 검정을 수행합니다.
    Args:
        data: pandas DataFrame 또는 dict 컬럼-배열 형식
        target_column: 불량 여부 컬럼명
        positive_label: 불량(양성)을 나타내는 값 (기본 1)
        group_column: 그룹화할 범주형 컬럼명 (None이면 전체 불량률만)
        min_samples: 그룹 통계 포함 최소 샘플 수 (group_column 지정 시)
        output_dir: 통계 JSON을 저장할 디렉터리 (예: artifacts/metrics)
    Returns:
        {"overall_rate": float, "overall_count": int,
         "group_rates": dict, "group_counts": dict, "chi2_pvalue": float,
         "artifact_path": str}
    """
    import pandas as pd
    import numpy as np
    from pathlib import Path
    from uuid import uuid4
    import json

    # target을 이진 분류로 변환
    if data is None:
        raise ValueError("data를 입력해주세요.")
    if target_column is None:
        raise ValueError("target_column을 입력해주세요.")

    if isinstance(data, pd.DataFrame):
        df = data.copy()
    elif isinstance(data, dict):
        df = pd.DataFrame(data)
    else:
        raise TypeError("data는 pandas DataFrame 또는 dict 형식이어야 합니다.")

    if target_column not in df.columns:
        raise ValueError(f"데이터에 없는 target_column입니다: {target_column}")
    if group_column is not None and group_column not in df.columns:
        raise ValueError(f"데이터에 없는 group_column입니다: {group_column}")

    target = df[target_column].eq(positive_label).astype(int)
    overall_count = int(target.count())
    overall_rate = float(target.mean()) if overall_count else float("nan")

    group_rates = {}
    group_counts = {}
    chi2_pvalue = None

    # if group_column is not None:
    if group_column is not None:

        # 결측치 그룹 제외
        group_data = pd.DataFrame(
            {
                "group": df[group_column],
                "target": target,
            }
        ).dropna(subset=["group"])

        # 카이제곱 검정 (그룹 간 불량률 차이 유의성)
        grouped = group_data.groupby("group", dropna=True)["target"]
        raw_counts = grouped.count()
        raw_rates = grouped.mean()

        try:
            min_samples = int(min_samples)
        except (TypeError, ValueError) as exc:
            raise ValueError("min_samples는 정수로 변환 가능한 값이어야 합니다.") from exc
        if min_samples < 1:
            raise ValueError("min_samples는 1 이상이어야 합니다.")

        # min_samples 이상인 그룹만 필터링된 통계
        selected_groups = raw_counts[raw_counts >= min_samples].index
        group_counts = {
            str(group): int(raw_counts.loc[group])
            for group in selected_groups
        }
        group_rates = {
            str(group): float(raw_rates.loc[group])
            for group in selected_groups
        }

        if len(selected_groups) >= 2:
            contingency = []
            group_sums = grouped.sum()
            for group in selected_groups:
                count = int(raw_counts.loc[group])
                failures = int(group_sums.loc[group])
                contingency.append([failures, count - failures])
            if np.asarray(contingency).sum() > 0:
                from scipy.stats import chi2_contingency

                _, chi2_pvalue, _, _ = chi2_contingency(contingency)
                chi2_pvalue = float(chi2_pvalue)

    result = {
        "overall_rate": overall_rate,
        "overall_count": overall_count,
        "group_rates": group_rates,
        "group_counts": group_counts,
        "chi2_pvalue": chi2_pvalue,
        "artifact_path": None,
    }

    if output_dir is not None:
        save_dir = Path(output_dir).expanduser().resolve()
        save_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = save_dir / f"compute_failure_rate__summary__{uuid4().hex}.json"
        result["artifact_path"] = str(artifact_path)
        artifact_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return result
